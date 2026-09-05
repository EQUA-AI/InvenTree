"""The authorized synchronous repository for durable chat history.

All public lookup operations apply the actor, server scope, and namespace
boundary before resolving a caller-supplied identifier. Async callers must use
``asgiref.sync.sync_to_async(..., thread_sensitive=True)`` around these methods.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, models, transaction
from django.db.models import F, QuerySet
from django.utils import timezone

from aichat.models import (
    ChatMessage,
    ChatThread,
    ChatTurn,
    MessageRole,
    ThreadNamespace,
    TurnModality,
    TurnState,
    generate_thread_id,
)

logger = logging.getLogger(__name__)


def _pack_ids(value: object) -> tuple[str, ...]:
    """Content-free pack ids from a persisted ``metadata['tool_packs']`` value."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


class ThreadRepositoryError(Exception):
    """Base class for safe repository errors."""


class AnonymousActorRejected(ThreadRepositoryError):  # noqa: N818
    """Raised when no authenticated boundary principal is supplied."""


class InvalidBoundary(ThreadRepositoryError, ValueError):  # noqa: N818
    """Raised when the trusted scope or namespace boundary is malformed."""


class ThreadNotFound(ThreadRepositoryError, LookupError):  # noqa: N818
    """Raised for every thread identifier outside the repository boundary."""


class ScopedThreadRejected(ThreadNotFound):
    """Raised when a scoped identifier reaches a legacy unscoped repository."""


class IdempotencyConflict(ThreadRepositoryError):  # noqa: N818
    """Raised when one key is reused with a different request fingerprint."""


class TurnStateConflict(ThreadRepositoryError):  # noqa: N818
    """Raised when a terminal turn is asked to transition or change result."""


class ScopeVersionConflict(ThreadRepositoryError):  # noqa: N818
    """Raised when a scope update carries a stale expected version (S1)."""


class ScopeUpdateRejected(ThreadRepositoryError):  # noqa: N818
    """Raised when a scope update fails authorization — deliberately generic.

    The message never discloses which candidate failed or whether it exists;
    the previous scope is preserved unchanged (decision record Q6).
    """


#: Key under which ``begin_turn`` snapshots the thread's analysis scope into
#: the stored turn context. Added only when a scope is actually set, so
#: unscoped turns keep the exact client-supplied trusted context.
ANALYSIS_SCOPE_SNAPSHOT_KEY = 'analysis_scope_snapshot'


@dataclass(frozen=True, slots=True)
class BeginTurnResult:
    """Result of beginning a new turn or replaying an existing turn."""

    turn: ChatTurn
    replayed: bool
    #: The immutable analysis-scope snapshot bound to this turn (S1). None
    #: for a thread without typed scope. On replay this is the ORIGINAL
    #: turn's stored snapshot — a later scope change never rebinds a turn.
    scope_snapshot: dict[str, Any] | None = None


def scope_fingerprint(scope_key: str) -> str:
    """Return the SHA-256 fingerprint of an exact server-derived scope key."""
    if not isinstance(scope_key, str) or not scope_key:
        raise InvalidBoundary('A non-empty server scope key is required')

    return hashlib.sha256(scope_key.encode('utf-8')).hexdigest()


def canonical_request_fingerprint(
    *,
    content: str,
    modality: str,
    trusted_context: Mapping[str, Any],
    modality_metadata: Mapping[str, Any] | None = None,
) -> str:
    """Hash the normalized inputs which define exact turn replay."""
    envelope = {
        'content': content,
        'modality': modality,
        'trusted_context': trusted_context,
        'modality_metadata': modality_metadata or {},
    }
    canonical = json.dumps(
        envelope, ensure_ascii=False, separators=(',', ':'), sort_keys=True
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


class ThreadRepository:
    """Boundary-bound repository and sole authorized chat persistence API."""

    def __init__(
        self, actor: Any, scope_key: str, namespace: str = ThreadNamespace.UNSCOPED
    ) -> None:
        """Bind all subsequent operations to an authenticated boundary."""
        actor_id: Any
        if isinstance(actor, bool) or actor is None:
            raise AnonymousActorRejected('An authenticated actor is required')
        if isinstance(actor, (int, str)):
            actor_id = actor
        else:
            if not getattr(actor, 'is_authenticated', False):
                raise AnonymousActorRejected('An authenticated actor is required')
            actor_id = getattr(actor, 'pk', None)
        if actor_id is None or actor_id == '':
            raise AnonymousActorRejected('An authenticated actor is required')
        if namespace not in ThreadNamespace.values:
            raise InvalidBoundary('Unknown chat thread namespace')

        self.actor = actor
        self.actor_id = actor_id
        self.scope_key = scope_key
        self.scope_hash = scope_fingerprint(scope_key)
        self.namespace = namespace

    def _threads(self) -> QuerySet[ChatThread]:
        """Return the non-negotiable authorization boundary queryset."""
        return ChatThread.objects.filter(
            owner_id=self.actor_id,
            scope_key=self.scope_key,
            scope_hash=self.scope_hash,
            namespace=self.namespace,
        )

    def _reject_wrong_namespace_id(self, thread_id: str) -> None:
        """Fail closed on the permanently reserved scoped-rail id prefix.

        The scoped namespace was dropped with its rail (S14c); a stale
        ``scoped_`` identifier must refuse rather than resolve here.
        """
        if thread_id.startswith('scoped_'):
            raise ScopedThreadRejected('Scoped threads are unavailable here')

    def _get_thread(self, thread_id: str) -> ChatThread:
        """Resolve a thread only after applying the complete boundary."""
        self._reject_wrong_namespace_id(thread_id)
        try:
            return self._threads().get(pk=thread_id)
        except ChatThread.DoesNotExist as exc:
            raise ThreadNotFound('Thread not found') from exc

    def _lock_thread(self, thread_id: str) -> ChatThread:
        """Resolve and lock a parent thread under the complete boundary."""
        self._reject_wrong_namespace_id(thread_id)
        try:
            return self._threads().select_for_update().get(pk=thread_id)
        except ChatThread.DoesNotExist as exc:
            raise ThreadNotFound('Thread not found') from exc

    def get_or_create(
        self, thread_id: str | None = None, *, title: str = ''
    ) -> tuple[ChatThread, bool]:
        """Get a boundary-visible thread or create it without claiming collisions."""
        if thread_id is None:
            thread_id = generate_thread_id()
        if not isinstance(thread_id, str) or not thread_id or len(thread_id) > 80:
            raise InvalidBoundary('Thread identifier is invalid')
        if not isinstance(title, str) or len(title) > 255:
            raise InvalidBoundary('Thread title is invalid')
        self._reject_wrong_namespace_id(thread_id)

        existing = self._threads().filter(pk=thread_id).first()
        if existing is not None:
            return existing, False

        try:
            with transaction.atomic():
                thread = ChatThread.objects.create(
                    id=thread_id,
                    owner_id=self.actor_id,
                    scope_key=self.scope_key,
                    scope_hash=self.scope_hash,
                    namespace=self.namespace,
                    title=title,
                )
        except IntegrityError as exc:
            existing = self._threads().filter(pk=thread_id).first()
            if existing is not None:
                return existing, False
            raise ThreadNotFound('Thread not found') from exc

        return thread, True

    def list(self) -> builtins.list[ChatThread]:
        """Return materialized threads within the complete boundary."""
        # ``builtins.list``: in annotations here ``list`` would otherwise
        # resolve to this method, which shadows the builtin in the class body.
        return list(self._threads())

    def search(self, query: str, *, limit: int = 50) -> builtins.list[ChatThread]:
        """Search titles and message content within the complete boundary (S20).

        Built on ``_threads()`` so the owner/scope boundary is the queryset
        base, not a filter someone can forget: user B's threads can never
        match user A's query. Ordering matches ``list`` (most recent first).
        """
        term = str(query or '').strip()[:200]
        if not term:
            return self.list()[: max(1, min(int(limit), 100))]
        rows = (
            self
            ._threads()
            .filter(
                models.Q(title__icontains=term)
                | models.Q(messages__content__icontains=term)
            )
            .distinct()
        )
        return list(rows[: max(1, min(int(limit), 100))])

    def get(self, thread_id: str) -> ChatThread:
        """Return one thread within the complete boundary."""
        return self._get_thread(thread_id)

    def rename(self, thread_id: str, title: str) -> ChatThread:
        """Rename one boundary-visible thread."""
        if not isinstance(title, str) or len(title) > 255:
            raise InvalidBoundary('Thread title is invalid')
        with transaction.atomic():
            thread = self._lock_thread(thread_id)
            thread.title = title
            thread.save(update_fields=['title', 'updated_at'])
        return thread

    def delete(self, thread_id: str) -> None:
        """Purge one boundary-visible transcript through the retention path.

        The retention service (S16) is the only correct deletion path: a
        naked ``thread.delete()`` raises ``ProtectedError`` once any
        ``ChatThreadGrant`` exists, and the immediate-deletion contract
        requires the non-content tombstone, grant-audit transfer, proposal
        scrub, voice cleanup, and upload-directory removal.
        """
        from aichat.services import retention

        thread = self._get_thread(thread_id)
        retention.purge_thread_now(
            thread.pk,
            actor_user_id=thread.owner_id,
            reason=retention.TOMBSTONE_USER_DELETE,
        )

    def _append_locked(
        self,
        thread: ChatThread,
        *,
        role: str,
        content: str,
        modality: str,
        metadata: Mapping[str, Any] | None,
        correlation_id: str,
    ) -> ChatMessage:
        """Allocate and append a message while the parent row is locked."""
        message = ChatMessage.objects.create(
            thread=thread,
            sequence=thread.next_sequence,
            role=role,
            content=content,
            modality=modality,
            metadata=dict(metadata or {}),
            correlation_id=correlation_id,
        )
        thread.next_sequence += 1
        thread.save(update_fields=['next_sequence', 'updated_at'])
        return message

    @staticmethod
    def _validate_message(
        *, role: str, content: str, modality: str, correlation_id: str
    ) -> None:
        """Validate portable scalar message values before persistence."""
        if role not in MessageRole.values:
            raise InvalidBoundary('Unknown chat message role')
        if modality not in TurnModality.values:
            raise InvalidBoundary('Unknown turn modality')
        if not isinstance(content, str):
            raise InvalidBoundary('Message content must be text')
        if not isinstance(correlation_id, str) or len(correlation_id) > 100:
            raise InvalidBoundary('Correlation identifier is invalid')

    def append(
        self,
        thread_id: str,
        *,
        role: str,
        content: str,
        modality: str = TurnModality.TEXT,
        metadata: Mapping[str, Any] | None = None,
        correlation_id: str = '',
    ) -> ChatMessage:
        """Append one exactly ordered message to a boundary-visible thread."""
        self._validate_message(
            role=role, content=content, modality=modality, correlation_id=correlation_id
        )
        with transaction.atomic():
            thread = self._lock_thread(thread_id)
            return self._append_locked(
                thread,
                role=role,
                content=content,
                modality=modality,
                metadata=metadata,
                correlation_id=correlation_id,
            )

    @staticmethod
    def _validate_turn_input(
        *,
        content: str,
        modality: str,
        idempotency_key: str,
        request_fingerprint: str,
        correlation_id: str,
    ) -> None:
        """Validate normalized turn scalars before opening a transaction."""
        ThreadRepository._validate_message(
            role=MessageRole.USER,
            content=content,
            modality=modality,
            correlation_id=correlation_id,
        )
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key
            or len(idempotency_key) > 255
        ):
            raise InvalidBoundary('Idempotency key is invalid')
        if (
            not isinstance(request_fingerprint, str)
            or len(request_fingerprint) != 64
            or any(char not in '0123456789abcdef' for char in request_fingerprint)
        ):
            raise InvalidBoundary('Request fingerprint must be a SHA-256 hex digest')

    def begin_turn(
        self,
        thread_id: str,
        *,
        content: str,
        modality: str,
        trusted_context: Mapping[str, Any],
        modality_metadata: Mapping[str, Any] | None,
        idempotency_key: str,
        request_fingerprint: str,
        correlation_id: str,
    ) -> BeginTurnResult:
        """Begin once, replay exactly, or reject a conflicting key reuse."""
        self._validate_turn_input(
            content=content,
            modality=modality,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            correlation_id=correlation_id,
        )
        if not isinstance(trusted_context, Mapping):
            raise InvalidBoundary('Trusted context must be an object')
        if modality_metadata is not None and not isinstance(modality_metadata, Mapping):
            raise InvalidBoundary('Modality metadata must be an object')

        with transaction.atomic():
            thread = self._lock_thread(thread_id)
            existing = (
                ChatTurn.objects
                .select_for_update()
                .filter(thread=thread, idempotency_key=idempotency_key)
                .first()
            )
            if existing is not None:
                if existing.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflict(
                        'Idempotency key was used for a different request'
                    )
                stored = existing.trusted_context or {}
                return BeginTurnResult(
                    turn=existing,
                    replayed=True,
                    scope_snapshot=stored.get(ANALYSIS_SCOPE_SNAPSHOT_KEY),
                )

            # S1: snapshot the active analysis scope under the SAME row lock
            # that creates the turn — one atomic operation, so a concurrent
            # scope update lands strictly before or strictly after this turn
            # and can never change what this turn was bound to. The snapshot
            # is stored server-side; the client-supplied trusted context is
            # persisted verbatim for unscoped threads (fingerprints cover
            # client inputs only, so the snapshot never affects replay
            # identity).
            scope_snapshot: dict[str, Any] | None = None
            if thread.analysis_scope_version > 0:
                scope_snapshot = {
                    'scope': dict(thread.analysis_scope or {}),
                    'version': thread.analysis_scope_version,
                    'hash': thread.analysis_scope_hash,
                }
            stored_context = dict(trusted_context)
            if scope_snapshot is not None:
                stored_context[ANALYSIS_SCOPE_SNAPSHOT_KEY] = scope_snapshot

            input_message = self._append_locked(
                thread,
                role=MessageRole.USER,
                content=content,
                modality=modality,
                metadata=modality_metadata,
                correlation_id=correlation_id,
            )
            turn = ChatTurn.objects.create(
                thread=thread,
                input_message=input_message,
                modality=modality,
                request_fingerprint=request_fingerprint,
                idempotency_key=idempotency_key,
                trusted_context=stored_context,
                modality_metadata=dict(modality_metadata or {}),
                correlation_id=correlation_id,
            )
            return BeginTurnResult(
                turn=turn, replayed=False, scope_snapshot=scope_snapshot
            )

    def _turn_thread_id(self, turn_id: str) -> str:
        """Resolve a turn's parent id only through the complete boundary."""
        thread_id = (
            ChatTurn.objects
            .filter(
                pk=turn_id,
                thread__owner_id=self.actor_id,
                thread__scope_key=self.scope_key,
                thread__scope_hash=self.scope_hash,
                thread__namespace=self.namespace,
            )
            .values_list('thread_id', flat=True)
            .first()
        )
        if thread_id is None:
            raise ThreadNotFound('Turn not found')
        return thread_id

    def terminal(
        self,
        turn_id: str,
        *,
        state: str,
        canonical_result: Mapping[str, Any],
        output_content: str | None = None,
        output_metadata: Mapping[str, Any] | None = None,
        workflow_id: str = '',
        evidence_sets: Sequence[Mapping[str, Any]] | None = None,
    ) -> ChatTurn:
        """Atomically persist the exact output and one terminal transition.

        ``evidence_sets`` (S10) are the analysis executor's pre-minted
        ``ChatEvidenceSet`` row specs; they are written INSIDE this
        transaction so failed/canceled turns leave no orphan evidence and a
        mid-write crash rolls the terminal row back with them.
        """
        if state not in TurnState.terminal_values():
            raise TurnStateConflict('A terminal state is required')
        if not isinstance(canonical_result, Mapping):
            raise InvalidBoundary('Canonical result must be an object')
        if output_content is None:
            output_content = str(canonical_result.get('detailed_response', ''))
        self._validate_message(
            role=MessageRole.ASSISTANT,
            content=output_content,
            modality=TurnModality.TEXT,
            correlation_id='',
        )
        if not isinstance(workflow_id, str) or len(workflow_id) > 100:
            raise InvalidBoundary('Workflow identifier is invalid')

        with transaction.atomic():
            thread_id = self._turn_thread_id(turn_id)
            thread = self._lock_thread(thread_id)
            try:
                turn = ChatTurn.objects.select_for_update().get(
                    pk=turn_id, thread=thread
                )
            except ChatTurn.DoesNotExist as exc:
                raise ThreadNotFound('Turn not found') from exc

            result = dict(canonical_result)
            if turn.is_terminal:
                same_output = (
                    turn.output_message is not None
                    and turn.output_message.content == output_content
                )
                if (
                    turn.state == state
                    and turn.canonical_result == result
                    and same_output
                ):
                    return turn
                raise TurnStateConflict('Turn already has a different terminal result')

            metadata = dict(output_metadata or {})
            if workflow_id:
                metadata.setdefault('workflow_id', workflow_id)
            output_message = self._append_locked(
                thread,
                role=MessageRole.ASSISTANT,
                content=output_content,
                modality=TurnModality.TEXT,
                metadata=metadata,
                correlation_id=turn.correlation_id,
            )
            turn.output_message = output_message
            turn.canonical_result = result
            turn.state = state
            turn.completed_at = timezone.now()
            turn.save(
                update_fields=[
                    'output_message',
                    'canonical_result',
                    'state',
                    'completed_at',
                    'updated_at',
                ]
            )
            if workflow_id:
                thread.last_workflow = workflow_id
                thread.save(update_fields=['last_workflow', 'updated_at'])
            if evidence_sets:
                # Fresh-write path only: the replay guard above already
                # returned, so a replayed terminal never double-writes sets.
                self._persist_evidence_sets(turn, evidence_sets)
            # S38: fresh-write path only — the replay guard above returned
            # already, so a replayed terminal can never re-trigger.
            self._maybe_schedule_compaction(thread)
            return turn

    def _persist_evidence_sets(
        self, turn: ChatTurn, specs: Sequence[Mapping[str, Any]]
    ) -> None:
        """Create evidence-set rows + members inside the caller's transaction."""
        from aichat.models import ChatEvidenceSet, ChatEvidenceSetMember

        for spec in specs:
            members = list(spec.get('members') or ())
            member_cap = int(spec.get('member_cap') or 25000)
            if len(members) > member_cap:
                raise InvalidBoundary('Evidence-set membership exceeds its cap')
            ordinals = [int(member[0]) for member in members]
            if ordinals != list(range(1, len(ordinals) + 1)):
                raise InvalidBoundary('Evidence-set member ordinals must be dense')
            evidence_set = ChatEvidenceSet.objects.create(
                id=str(spec['id']),
                turn=turn,
                authorization_scope_hash=str(
                    spec.get('authorization_scope_hash') or ''
                ),
                analysis_scope_hash=str(spec.get('analysis_scope_hash') or ''),
                source_class=str(spec['source_class']),
                filters=dict(spec.get('filters') or {}),
                population_count=int(spec.get('population_count') or 0),
                evaluated_count=int(spec.get('evaluated_count') or 0),
                displayed_count=int(spec.get('displayed_count') or 0),
                complete_population=bool(spec.get('complete_population')),
                high_watermarks=dict(spec.get('high_watermarks') or {}),
                snapshot_hash=str(spec.get('snapshot_hash') or ''),
                supports_expansion=bool(spec.get('supports_expansion')),
                member_count=len(members),
                member_cap=member_cap,
                calculation=dict(spec.get('calculation') or {}),
            )
            ChatEvidenceSetMember.objects.bulk_create(
                (
                    ChatEvidenceSetMember(
                        set=evidence_set,
                        ordinal=int(ordinal),
                        source_class=str(source_class),
                        source_object_id=str(source_object_id),
                        source_version=str(source_version or ''),
                    )
                    for ordinal, source_class, source_object_id, source_version in members
                ),
                batch_size=1000,
            )

    #: S38: summarize rarely and in large chunks (prefix-cache stability) —
    #: only once this many messages sit above the watermark.
    COMPACTION_MIN_BACKLOG = 16

    def _maybe_schedule_compaction(self, thread) -> None:
        """Queue the compaction job when the un-summarized backlog is large.

        Best-effort and flag-gated (shadow or full). ``force_async`` keeps
        the LLM call off the request path: without workers the job simply
        waits for one instead of running inline.
        """
        try:
            from ai.core.config import get_settings

            settings = get_settings()
            if not (
                settings.feature_thread_compaction_shadow
                or settings.feature_thread_compaction
            ):
                return
            backlog = (thread.next_sequence - 1) - thread.summary_through_sequence
            if backlog < self.COMPACTION_MIN_BACKLOG:
                return
            from aichat import tasks as aichat_tasks
            from InvenTree.tasks import offload_task

            offload_task(
                aichat_tasks.compact_thread_summary, thread.pk, force_async=True
            )
        except Exception:
            logger.warning('Thread compaction scheduling failed (ignored)')

    def replay(
        self,
        thread_id: str,
        *,
        idempotency_key: str,
        request_fingerprint: str | None = None,
    ) -> ChatTurn:
        """Return a stored turn/result without disclosing another boundary."""
        thread = self._get_thread(thread_id)
        try:
            turn = ChatTurn.objects.select_related(
                'input_message', 'output_message'
            ).get(thread=thread, idempotency_key=idempotency_key)
        except ChatTurn.DoesNotExist as exc:
            raise ThreadNotFound('Turn not found') from exc
        if (
            request_fingerprint is not None
            and turn.request_fingerprint != request_fingerprint
        ):
            raise IdempotencyConflict(
                'Idempotency key was used for a different request'
            )
        return turn

    def messages(self, thread_id: str) -> builtins.list[ChatMessage]:
        """Return a materialized, ordered transcript inside the boundary."""
        thread = self._get_thread(thread_id)
        return list(ChatMessage.objects.filter(thread=thread))

    # ---- S1: server-owned active analysis scope --------------------------
    #
    # The scope narrows analysis retrieval; it authorizes nothing. Reads
    # follow the same visibility as transcripts (owner, or shared read);
    # writes stay owner-only because they ride ``_lock_thread``, exactly
    # like rename/delete. Shape validation lives in
    # ``ai.core.analysis.scope`` (stdlib-only, both planes import it).

    def _actor_user(self):
        """The acting Django user, for per-record authorization checks."""
        if getattr(self.actor, 'is_authenticated', False):
            return self.actor
        from django.contrib.auth import get_user_model

        return get_user_model().objects.filter(pk=self.actor_id, is_active=True).first()

    def _scope_payload(self, thread: ChatThread, *, editable: bool) -> dict[str, Any]:
        """Project one thread's stored scope for the wire."""
        from ai.core.analysis import scope as scope_contract

        stored = scope_contract.scope_from_stored(thread.analysis_scope)
        return {
            'thread_id': thread.pk,
            'scope': scope_contract.scope_to_payload(stored),
            'version': thread.analysis_scope_version,
            'hash': thread.analysis_scope_hash,
            'display_label': scope_contract.display_summary(stored),
            'editable': editable,
        }

    def get_scope(self, thread_id: str) -> dict[str, Any]:
        """Return the active analysis scope (owner, or shared read-only)."""
        thread, shared = self.get_readable(thread_id)
        return self._scope_payload(thread, editable=not shared)

    def scope_summary(self, thread: ChatThread) -> dict[str, Any]:
        """Compact scope projection for list/detail rows (no extra queries)."""
        from ai.core.analysis import scope as scope_contract

        stored = scope_contract.scope_from_stored(thread.analysis_scope)
        return {
            'mode': stored.mode,
            'version': thread.analysis_scope_version,
            'display_label': scope_contract.display_summary(stored),
        }

    def set_scope(
        self,
        thread_id: str,
        requested_scope: Mapping[str, Any],
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        """Replace the analysis scope under optimistic concurrency.

        Explicit machine ids are re-authorized against the acting principal
        before anything is stored; one unauthorized (or unknown — the two
        are indistinguishable) id rejects the entire update generically and
        preserves the previous scope. A stale ``expected_version`` raises
        ``ScopeVersionConflict`` before any write. Per-turn reauthorization
        at intake covers permissions revoked after this update.
        """
        from ai.core.analysis import scope as scope_contract

        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            raise InvalidBoundary('expected_version must be an integer')

        normalized = scope_contract.normalize_scope_request(requested_scope)

        if normalized.mode == scope_contract.MODE_EXPLICIT:
            actor_user = self._actor_user()

            def authorize(machine_id: int) -> bool:
                """One candidate id is readable by the actor right now."""
                if actor_user is None:
                    return False
                try:
                    from assets.ai_read import authorized_machine
                except ImportError:
                    return False
                return authorized_machine(actor_user, machine_id) is not None

            try:
                scope_contract.require_all_authorized(normalized.machine_ids, authorize)
            except scope_contract.ScopeRejected as exc:
                raise ScopeUpdateRejected('Scope update rejected') from exc

        payload = scope_contract.scope_to_payload(normalized)
        digest = scope_contract.scope_hash(normalized)

        with transaction.atomic():
            thread = self._lock_thread(thread_id)
            if thread.analysis_scope_version != expected_version:
                raise ScopeVersionConflict('Scope version is stale')
            thread.analysis_scope = payload
            thread.analysis_scope_version += 1
            thread.analysis_scope_hash = digest
            thread.save(
                update_fields=[
                    'analysis_scope',
                    'analysis_scope_version',
                    'analysis_scope_hash',
                    'updated_at',
                ]
            )
        return self._scope_payload(thread, editable=True)

    # ---- S32b (B6): explicit read-only sharing ---------------------------
    #
    # Grants widen EXACTLY two things: resolving one named thread for a read
    # (``get_readable``) and the "shared with me" listing (``list_shared``).
    # ``_threads()``, ``_get_thread`` and ``_lock_thread`` stay owner-only,
    # so every write path — rename, delete, append, begin_turn, terminal —
    # is untouched by a grant, which is what makes READ access read-only.

    @staticmethod
    def _sharing_enabled() -> bool:
        """Whether thread sharing exists in this deployment (fail closed)."""
        from django.conf import settings as django_settings

        return bool(getattr(django_settings, 'FEATURE_THREAD_SHARING', False))

    def _active_grants(self):
        """Grants currently conferring read access to the acting user."""
        from django.utils import timezone

        from aichat.models import ChatThreadGrant

        return ChatThreadGrant.objects.filter(
            grantee_id=self.actor_id, revoked_at__isnull=True
        ).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
        )

    def _get_shared_thread(self, thread_id: str) -> ChatThread:
        """Resolve a thread readable through an explicit active grant.

        The grant must land inside the SAME scope boundary this repository
        was constructed for; a grant can never carry a thread across scopes.
        """
        if not self._sharing_enabled():
            raise ThreadNotFound('Thread not found')
        self._reject_wrong_namespace_id(thread_id)
        grant = (
            self
            ._active_grants()
            .filter(
                thread_id=thread_id,
                thread__scope_key=self.scope_key,
                thread__scope_hash=self.scope_hash,
                thread__namespace=self.namespace,
            )
            .select_related('thread')
            .first()
        )
        if grant is None:
            raise ThreadNotFound('Thread not found')
        return grant.thread

    def get_readable(self, thread_id: str) -> tuple[ChatThread, bool]:
        """Return one READABLE thread: owned, or explicitly granted.

        The second element reports whether the access came from a grant, so
        callers can label shared transcripts and withhold write affordances.
        """
        try:
            return self._get_thread(thread_id), False
        except ThreadNotFound:
            return self._get_shared_thread(thread_id), True

    def readable_messages(self, thread_id: str) -> builtins.list[ChatMessage]:
        """Return the transcript of one readable (owned or granted) thread."""
        thread, _ = self.get_readable(thread_id)
        return list(ChatMessage.objects.filter(thread=thread))

    def evidence_set(self, thread_id: str, set_id: str):
        """Resolve one evidence set inside the readable-thread boundary (S10).

        Every failure mode — unknown thread, unknown set, a set belonging to
        another thread — raises the same ``ThreadNotFound`` so the caller's
        generic 404 discloses nothing.
        """
        from aichat.models import ChatEvidenceSet

        thread, _ = self.get_readable(thread_id)
        row = (
            ChatEvidenceSet.objects
            .filter(pk=str(set_id), turn__thread=thread)
            .select_related('turn')
            .first()
        )
        if row is None:
            raise ThreadNotFound('Evidence set not found')
        return row

    def evidence_set_members(
        self, thread_id: str, set_id: str, *, after_ordinal: int = 0, limit: int = 50
    ) -> builtins.list[dict[str, Any]]:
        """One page of members, each reauthorized LIVE for the actor (§7.6).

        A member the actor can no longer read — revoked, deleted, or of an
        unknown source class — projects only ``{member_index, available:
        false}``-grade fields; the causes are indistinguishable by design.
        Labels resolve from the live record (no text is stored to leak).
        Digest-only sets 404 generically: expansion was never promised.
        """
        row = self.evidence_set(thread_id, set_id)
        if not row.supports_expansion:
            raise ThreadNotFound('Evidence set not found')
        actor_user = self._actor_user()
        members = list(
            row.members.filter(ordinal__gt=int(after_ordinal)).order_by('ordinal')[
                : max(1, int(limit))
            ]
        )
        projected: builtins.list[dict[str, Any]] = []
        for member in members:
            label = self._resolve_member_label(
                actor_user, member.source_class, member.source_object_id
            )
            available = label is not None
            projected.append({
                'member_index': member.ordinal,
                'source_class': member.source_class,
                'source_object_id': member.source_object_id if available else None,
                'label': label,
                'available': available,
            })
        return projected

    @staticmethod
    def _resolve_member_label(actor_user, source_class: str, object_id: str):
        """Live per-record reauthorization; ``None`` means not available.

        Unknown source classes fail closed — a class this method cannot
        reauthorize is never shown.
        """
        if actor_user is None:
            return None
        try:
            if source_class == 'work_order':
                from tasks.ai_read import authorized_work_order

                work_order = authorized_work_order(actor_user, object_id)
                if work_order is None:
                    return None
                return work_order.reference or f'Work order {work_order.pk}'
            if source_class in ('machine', 'asset_machine'):
                from assets.ai_read import authorized_machine

                machine = authorized_machine(actor_user, object_id)
                if machine is None:
                    return None
                return machine.name
            if source_class == 'maintenance_record':
                from tasks.ai_analytics import authorized_maintenance_record

                record = authorized_maintenance_record(actor_user, object_id)
                if record is None:
                    return None
                return f'{record.date.isoformat()} — {record.summary}'[:255]
        except ImportError:
            return None
        return None

    def list_shared(self) -> builtins.list[ChatThread]:
        """Threads shared with the actor inside this scope boundary."""
        if not self._sharing_enabled():
            return []
        rows = (
            self
            ._active_grants()
            .filter(
                thread__scope_key=self.scope_key,
                thread__scope_hash=self.scope_hash,
                thread__namespace=self.namespace,
            )
            .select_related('thread')
            .order_by('-thread__updated_at')
        )
        seen: set[str] = set()
        threads: builtins.list[ChatThread] = []
        for grant in rows:
            if grant.thread_id not in seen:
                seen.add(grant.thread_id)
                threads.append(grant.thread)
        return threads

    def share(self, thread_id: str, *, grantee_id: int, expires_at=None):
        """Grant read access on an OWNED thread; idempotent per active grant."""
        from django.contrib.auth import get_user_model

        from aichat.models import ChatThreadGrant

        if not self._sharing_enabled():
            raise InvalidBoundary('Thread sharing is disabled')
        thread = self._get_thread(thread_id)  # owner-only resolution
        if int(grantee_id) == int(self.actor_id):
            raise InvalidBoundary('A thread cannot be shared with its owner')
        grantee = get_user_model().objects.filter(pk=grantee_id, is_active=True).first()
        if grantee is None:
            raise InvalidBoundary('Grantee is unknown or inactive')
        existing = (
            self
            ._filtered_thread_grants(thread, grantee_id)
            .filter(revoked_at__isnull=True)
            .first()
        )
        if existing is not None:
            return existing
        return ChatThreadGrant.objects.create(
            thread=thread,
            grantee=grantee,
            granted_by_id=self.actor_id,
            expires_at=expires_at,
        )

    def revoke_share(self, thread_id: str, *, grantee_id: int) -> int:
        """Revoke active grants on an OWNED thread; rows are never deleted."""
        from django.utils import timezone

        if not self._sharing_enabled():
            raise InvalidBoundary('Thread sharing is disabled')
        thread = self._get_thread(thread_id)  # owner-only resolution
        return (
            self
            ._filtered_thread_grants(thread, grantee_id)
            .filter(revoked_at__isnull=True)
            .update(revoked_at=timezone.now())
        )

    @staticmethod
    def _filtered_thread_grants(thread: ChatThread, grantee_id: int):
        """All grant rows for one (thread, grantee) pair."""
        from aichat.models import ChatThreadGrant

        return ChatThreadGrant.objects.filter(thread=thread, grantee_id=grantee_id)

    def thread_grants(self, thread_id: str):
        """Active grants on an OWNED thread (for the share UI)."""
        thread = self._get_thread(thread_id)
        return list(self._active_grants_for_thread(thread).select_related('grantee'))

    def _active_grants_for_thread(self, thread: ChatThread):
        """Active (unrevoked, unexpired) grants on one thread."""
        from django.utils import timezone

        from aichat.models import ChatThreadGrant

        return ChatThreadGrant.objects.filter(
            thread=thread, revoked_at__isnull=True
        ).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
        )

    def recall_window(self, thread_id: str, *, limit: int, exclude_latest: int = 1):
        """M1 (GR-31 seat 1): the replay window AND the summary in ONE statement.

        The boundary is applied in SQL (a subquery over ``_threads()``), the
        thread's summary/watermark/next_sequence ride as annotations on every
        row, so the builder never pays a second round trip for the note.
        Newest ``limit`` rows after skipping ``exclude_latest`` (the turn's
        own user message), returned oldest-first. A thread outside the
        boundary or with no earlier messages yields an empty window.
        """
        from ai.core.memory.context_assembler import RecallRow, RecallWindow

        self._reject_wrong_namespace_id(thread_id)
        if limit <= 0:
            return RecallWindow(thread_id=thread_id)
        rows = list(
            ChatMessage.objects
            .filter(thread__in=self._threads().filter(pk=thread_id))
            .annotate(
                thread_summary=F('thread__summary'),
                thread_watermark=F('thread__summary_through_sequence'),
                thread_next=F('thread__next_sequence'),
            )
            .order_by('-sequence')
            .values(
                'sequence',
                'role',
                'content',
                'thread_summary',
                'thread_watermark',
                'thread_next',
                # M1 (GR-33): the packs each assistant turn ran with, one JSON
                # key of the row's metadata — same statement, no extra trip.
                'metadata__tool_packs',
            )[exclude_latest : exclude_latest + limit]
        )
        rows.reverse()
        if not rows:
            return RecallWindow(thread_id=thread_id)
        first = rows[0]
        return RecallWindow(
            thread_id=thread_id,
            rows=tuple(
                RecallRow(
                    int(r['sequence']),
                    str(r['role']),
                    str(r['content']),
                    tool_packs=_pack_ids(r.get('metadata__tool_packs')),
                )
                for r in rows
            ),
            summary=str(first['thread_summary'] or ''),
            watermark=int(first['thread_watermark'] or 0),
            next_sequence=int(first['thread_next'] or 0),
            db_round_trips=1,
        )

    def recent_messages(
        self, thread_id: str, limit: int, *, exclude_latest: int = 0
    ) -> builtins.list[ChatMessage]:
        """Return the newest `limit` messages, skipping the last `exclude_latest`.

        Bounded in SQL and returned oldest-first. A lookup turn replays only a
        short tail of the thread, so materializing the whole transcript to slice
        it in Python would grow with the age of the conversation.
        """
        if limit <= 0:
            return []
        thread = self._get_thread(thread_id)
        window = list(
            ChatMessage.objects.filter(thread=thread).order_by('-sequence')[
                exclude_latest : exclude_latest + limit
            ]
        )
        window.reverse()
        return window
