"""Single-focus, compare-and-set decision state machine.

Speech is input, never identity. Every effect requires current server bindings,
an exact client focus reference, a whole-utterance assent, and domain revalidation.
Playback callbacks can only affect delivery/lifetime, never grant authorization.
"""

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ai.core.decisions.grammar import DecisionUtteranceKind as Kind
from ai.core.decisions.grammar import classify_decision_utterance
from ai.core.decisions.models import DecisionDeliveryState as Delivery
from ai.core.decisions.models import DecisionKind, PendingDecision
from ai.core.decisions.models import DecisionState as State
from ai.core.decisions.pronunciation import spoken_target
from ai.core.decisions.resolver import WorkOrderIntent, apply_correction, parse_work_order_intent
from ai.core.decisions.store import DecisionStoreUnavailable

ECHO_TAIL_SECONDS = 1.5


class DecisionConflict(ValueError):
    """Stale focus or playback evidence; no authorization is consumed."""


def estimated_playback_seconds(text: str) -> float:
    """Conservative 120-wpm fallback, including pauses and identifier spelling."""
    return max(2.0, len(text.split()) * 0.6 + len(re.findall(r"\d", text)) * 0.3 + 1.0)


def minimum_playback_seconds(text: str) -> float:
    """Reject physically implausible callbacks; this is not the longer echo guard."""
    return max(0.5, len(text.split()) * 0.15)


def _echo_text(text):
    return " ".join(re.findall(r"[\w]+", text.casefold()))


@dataclass(frozen=True)
class DecisionReply:
    """A server-authored message plus the authoritative interaction snapshot."""

    spoken: str
    decision: PendingDecision | None = None
    event: str = "status"
    route_normally: bool = False
    spoken_layout: str | None = None

    def event_dict(self):
        """Bound event used to decide whether this reply needs a read-back binding."""
        return {
            "kind": self.event,
            "decision_id": self.decision.decision_id if self.decision else None,
            "sequence": self.decision.sequence if self.decision else None,
            "message": self.spoken,
        }


class DecisionCoordinator:
    """Pure interaction policy around injected storage and domain adapters."""

    def __init__(
        self,
        *,
        store,
        adapter,
        approval_adapter=None,
        ttl_s=120,
        max_review_turns=3,
        max_armed_s=300,
        now=None,
    ):
        if not (1 <= ttl_s <= max_armed_s <= 300 and 0 <= max_review_turns <= 3):
            raise ValueError("Invalid decision lifetime limits")
        self.store = store
        self.adapter = adapter
        self.approvals = approval_adapter
        self.ttl_s = ttl_s
        self.max_review_turns = max_review_turns
        self.max_armed_s = max_armed_s
        self.now = now or (lambda: datetime.now(UTC))

    def advance(self, decision, **changes):
        """CAS is the only state transition; an old turn cannot replace new focus."""
        updated = replace(decision, sequence=decision.sequence + 1, **changes)
        if not self.store.replace_if(decision.thread_id, decision.sequence, updated):
            raise DecisionConflict("The decision changed. Read the current preview again.")
        return updated

    def disarm(self, thread_id, reason="disarmed", *, set_aside=False):
        """Invalidate interaction authority, retaining the durable source and ledger."""
        decision = self.store.read(thread_id)
        if decision and (
            (decision.executable or {}).get("adapter") == "capture_review"
            or str((decision.executable or {}).get("action", "")).startswith("closeout.")
        ):
            from ai.core.decisions.adapters.capture_review import invalidate

            invalidate(decision.session_id)
        if decision and decision.state == State.PRESENTED:
            decision = self.advance(
                decision, state=State.SET_ASIDE if set_aside else State.DISARMED
            )
        return DecisionReply(
            "The confirmation was set aside. No new action was submitted.", decision, reason
        )

    def present(self, intent, *, actor, session_id, thread_id, nonce, source_content, expected):
        """Create a fresh owner-bound proposal, then conditionally install its focus."""
        proposal = self.adapter.create(intent, actor=actor, thread_id=thread_id, nonce=nonce)
        preview = proposal.preview
        label = " ".join(
            str(preview.get(key) or "").strip() for key in ("reference", "title")
        ).strip()
        if not label:
            label = f"Work order {proposal.target_work_order_id}"
        if intent.action.startswith("stock."):
            label = f"{preview['part_name']} ({preview['IPN'] or 'no IPN'}), stock item {preview['stock_item_id'] or 'new'}"
        reason = str(preview.get("reason") or "").strip()
        is_cancel = intent.action == "work_order.cancel"
        if not reason and intent.action in (
            "work_order.hold",
            "work_order.cancel",
            "work_order.delete",
        ):
            raise DecisionConflict("Say the reason for the action and request a fresh preview.")
        eligible = len(reason) <= 300 and len(label) <= 160
        # Spell identifiers without changing quantity semantics elsewhere.
        spoken_label = spoken_target(label)
        required_phrase = str(preview.get("confirm_phrase") or "") if is_cancel else None
        if is_cancel and required_phrase != "confirm cancel order":
            raise DecisionConflict("Request a fresh cancellation preview with its strict phrase.")
        spoken = (
            (
                f"Cancel {spoken_label}. Reason: {reason}. This cannot be undone. "
                "This does not change any safety status. Say confirm cancel order to proceed. "
                "To keep the work order as it is, say no. Say change that to correct it."
                if is_cancel
                else f"Put {spoken_label} on hold. Reason: {reason}. "
                "This does not change any safety status. Say confirm hold or yes to proceed, "
                "change that to correct it, or cancel to set it aside."
            )
            if eligible
            else "This preview is too long for voice confirmation. Review it on screen."
        )
        sections = tuple(
            {"id": key, "label": key.replace("_", " ").title(), "text": str(preview.get(key) or "")}
            for key in ("current_status", "resulting_status", "reason", "warning")
        )
        allowed_responses = (
            ("confirm cancel order", "no", "change that", "cancel the action")
            if is_cancel
            else ("confirm hold", "yes", "change that", "cancel", "cancel the action")
        )
        if intent.action not in ("work_order.hold", "work_order.cancel"):
            if intent.action.startswith("stock."):
                from ai.core.decisions.inventory import review
            else:
                from ai.core.decisions.work_order_review import review

            spoken, sections, required_phrase = review(intent.action, preview, spoken_label)
            if intent.action in ("closeout.accept", "closeout.handoff") and preview.get(
                "auditory_review_id"
            ):
                # Every full-text page has durable exact playback evidence.
                # Keep the full note on screen, then arm a NEW bounded action
                # read-back with target, revision/hash, disclosure and phrase.
                from ai.core.decisions.work_order_review import VERBS

                spoken = (
                    f"{VERBS[intent.action]} {spoken_label}. "
                    f"Every page of note revision {preview['revision']} finished playing. "
                    f"Content hash: {preview['content_hash']}. {preview['warning']} "
                    f"Say {required_phrase} to proceed, or no to set it aside."
                )
            eligible = len(spoken) <= 1800 and len(label) <= 160
            allowed_responses = (required_phrase or "yes", "no", "change that", "cancel the action")
            if not eligible:
                spoken = (
                    "This preview needs a full on-screen review. No voice confirmation is armed."
                )
        now = self.now()
        decision = PendingDecision(
            decision_id=str(uuid4()),
            kind=DecisionKind.ACTION,
            source_id=str(proposal.id),
            revision=proposal.target_version,
            state=State.PRESENTED,
            target_label=label[:255],
            sections=sections,
            allowed_responses=allowed_responses,
            required_phrase=required_phrase,
            expires_at=now + timedelta(seconds=self.max_armed_s),
            sequence=1,
            actor_user_pk=str(actor.user_pk),
            session_id=str(session_id),
            thread_id=str(thread_id),
            scope_hash=proposal.scope_hash,
            nonce=str(nonce),
            armed_at=now,
            source_content=source_content[:4000],
            preview_hash=proposal.preview_hash,
            executable={
                "adapter": "proposal",
                "action": intent.action,
                "reference": intent.reference,
                "target_reference": str(preview.get("reference") or ""),
                "target_id": str(proposal.target_work_order_id),
                "reason": reason,
                "parameters": intent.parameters,
            },
            voice_eligible=eligible,
            voice_ineligible_reason=None if eligible else "Full on-screen review required.",
            locale="en-US",
            spoken_summary=spoken,
        )
        if not self.store.install(decision, expected):
            raise DecisionConflict(
                "A newer decision is active. Read its preview before continuing."
            )
        return DecisionReply(spoken, decision, "presented")

    def begin(self, content, *, actor, session_id, thread_id, nonce):
        """Deterministic work-order requests precede general workflow routing."""
        if re.match(
            r"^(?:split|merge|convert|install|uninstall|assign|unassign|serialize|change status of) (?:the )?stock\b",
            content.strip(),
            re.I,
        ):
            return DecisionReply(
                "Only adding, removing, transferring and counting stock are available by voice. Review this inventory action on screen.",
                event="unavailable",
            )
        if re.match(
            r"^(?:record|file|capture|start)(?: a)? fault (?:note|intake)\b", content.strip(), re.I
        ):
            return DecisionReply("Fault notes cannot be filed by voice yet.", event="unavailable")
        if re.match(
            r"^(?:start close\s*out|note |replace note |read the whole note|accept this note|hand\s*off this note|cancel close\s*out note|change .+ to )",
            content.strip(),
            re.I,
        ):
            from ai.core.config import get_settings

            if getattr(get_settings(), "feature_voice_closeout", False):
                from ai.core.decisions.adapters.capture_adapter import begin as begin_capture

                reply = begin_capture(
                    self,
                    content,
                    actor=actor,
                    session_id=session_id,
                    thread_id=thread_id,
                    nonce=nonce,
                )
                if reply is not None:
                    return reply
            elif not content.lower().startswith("change "):
                return DecisionReply("Closeout by voice is disabled.", event="unavailable")
        if self.approvals is not None:
            reply = self.approvals.begin(
                self, content, actor=actor, session_id=session_id, thread_id=thread_id, nonce=nonce
            )
            if reply is not None:
                return reply
        from ai.core.decisions.inventory import parse_stock_intent

        intent = parse_stock_intent(content) or parse_work_order_intent(content)
        if intent is None:
            return None
        if intent.action == "stock.read":
            from ai.core.decisions.inventory import stock_read_reply

            owner, _ = self.adapter.owner_scope(actor)
            return stock_read_reply(owner, intent.parameters)
        return self.present(
            intent,
            actor=actor,
            session_id=session_id,
            thread_id=thread_id,
            nonce=nonce,
            source_content=content,
            expected=self.store.read(thread_id),
        )

    def revalidate(self, decision, actor, session_id):
        """Read current source state; screen actions and drift invalidate voice focus."""
        if str(actor.user_pk) != decision.actor_user_pk or str(session_id) != decision.session_id:
            raise DecisionConflict("The session changed. Request a fresh preview.")
        if (decision.executable or {}).get("adapter") == "capture_review":
            from ai.core.decisions.adapters.capture_review import read

            return read(self, decision, actor, session_id)
        if (decision.executable or {}).get("adapter") == "approval":
            if self.approvals is None:
                raise DecisionConflict("Voice approvals are disabled.")
            return self.approvals.read(decision, actor)
        proposal = self.adapter.read(decision, actor)
        if proposal.state != "proposed":
            if decision.state == State.PRESENTED:
                self.advance(decision, state=State.DISARMED)
            raise DecisionConflict("This proposal was already decided. Read its recorded result.")
        if (
            proposal.target_version != decision.revision
            or proposal.preview_hash != decision.preview_hash
        ):
            self.advance(decision, state=State.DISARMED)
            raise DecisionConflict("The preview changed. Request a fresh preview.")
        return proposal

    @staticmethod
    def check_context(decision, context):
        """A reply must explicitly name the exact announced focus, not merely a thread."""
        if not context or any(
            context.get(key) != getattr(decision, key)
            for key in ("decision_id", "sequence", "revision", "preview_hash")
        ):
            raise DecisionConflict("That confirmation is stale. Read the current preview again.")

    def echo_window(self, decision, *, provider_active=False):
        """Client completion never shortens the independently estimated echo window."""
        if decision.playback_requested_at is None:
            return True
        minimum_end = decision.playback_requested_at + timedelta(
            seconds=estimated_playback_seconds(decision.spoken_summary) + ECHO_TAIL_SECONDS
        )
        end = minimum_end
        if decision.playback_completed_at:
            end = max(end, decision.playback_completed_at + timedelta(seconds=ECHO_TAIL_SECONDS))
        return provider_active or self.now() < end

    def resolve(
        self,
        content,
        *,
        actor,
        session_id,
        thread_id,
        nonce,
        context=None,
        provider_active=False,
        touch=False,
    ):
        """Resolve all transitions without invoking a model or trusting playback as assent."""
        decision = self.store.read(thread_id)
        if decision is None:
            if context:
                return DecisionReply(
                    "That decision is no longer available. Request a fresh preview.", event="stale"
                )
            return None
        if (decision.executable or {}).get("adapter") == "capture_review":
            from ai.core.decisions.adapters.capture_review import resolve

            return resolve(
                self,
                decision,
                content,
                actor=actor,
                session_id=session_id,
                thread_id=thread_id,
                nonce=nonce,
                context=context,
                provider_active=provider_active,
                touch=touch,
            )
        if (decision.executable or {}).get("adapter") == "approval":
            if self.approvals is None:
                raise DecisionConflict("Voice approvals are disabled.")
            self.check_context(decision, context)
            return self.approvals.resolve(
                self,
                decision,
                content,
                actor=actor,
                session_id=session_id,
                thread_id=thread_id,
                nonce=nonce,
                context=context,
                provider_active=provider_active,
                touch=touch,
            )
        kind = classify_decision_utterance(
            content,
            allowed_responses=decision.allowed_responses,
            required_phrase=decision.required_phrase,
            locale=decision.locale,
            target_references=tuple(
                str((decision.executable or {}).get(key) or "")
                for key in ("target_reference", "target_id")
                if (decision.executable or {}).get(key)
            ),
        )
        if context is not None:
            self.check_context(decision, context)
        if decision.state in (State.EXECUTING, State.RESOLVED):
            if kind in (Kind.HISTORY, Kind.AFFIRM, Kind.DECLINE, Kind.DISARM, Kind.CANCEL_ACTION):
                return self.history(decision, actor)
            return None
        if decision.state != State.PRESENTED:
            if kind == Kind.AFFIRM:
                return DecisionReply(
                    "Nothing is armed. Request a fresh preview.", decision, "disarmed"
                )
            return None
        if self.now() >= decision.expires_at:
            decision = self.advance(decision, state=State.EXPIRED)
            return DecisionReply(
                "The confirmation expired. Request a fresh preview.", decision, "expired"
            )
        try:
            self.revalidate(decision, actor, session_id)
        except Exception:
            current = self.store.read(thread_id)
            if current and current.state == State.PRESENTED:
                self.advance(current, state=State.DISARMED)
            raise
        echo_match = bool(_echo_text(content)) and (
            (kind == Kind.AFFIRM and decision.required_phrase == "confirm cancel order")
            or _echo_text(content) == _echo_text(decision.required_phrase or "")
            or f" {_echo_text(content)} " in f" {_echo_text(decision.spoken_summary)} "
        )
        if (
            not touch
            and kind in (Kind.AFFIRM, Kind.DECLINE)
            and echo_match
            and self.echo_window(decision, provider_active=provider_active)
        ):
            return DecisionReply(
                "Please wait until the read-back has finished, then give your response.",
                decision,
                "echo_refused",
            )
        if kind == Kind.AFFIRM:
            self.check_context(decision, context)
            if not decision.voice_eligible and not touch:
                return DecisionReply(
                    decision.voice_ineligible_reason or "Review this on screen.",
                    decision,
                    "ineligible",
                )
            if not touch and decision.playback_requested_at is None:
                return DecisionReply(
                    "The read-back has not been delivered. Review the decision on screen.",
                    decision,
                    "undelivered",
                )
            # The whole-utterance grammar verified the optional reference and
            # strict phrase. Dispatch only the canonical phrase, not a prefix
            # extracted from an arbitrary utterance.
            phrase = decision.required_phrase if decision.required_phrase else content
            return self.execute(decision, actor, phrase)
        if kind in (Kind.DECLINE, Kind.DISARM):
            return self.disarm(thread_id, "declined")
        if kind == Kind.CANCEL_ACTION:
            self.check_context(decision, context)
            decision = self.advance(decision, state=State.DISARMED)
            self.adapter.reject(decision, actor)
            return DecisionReply(
                "The proposed action was canceled. No business record was changed.",
                decision,
                "canceled",
            )
        if kind == Kind.AMEND:
            decision = self.advance(decision, state=State.DISARMED)
            original = decision.executable or {}
            intent = apply_correction(
                content,
                WorkOrderIntent(
                    str(original.get("reference", "")),
                    str(original.get("reason", "")),
                    str(original.get("action") or "work_order.hold"),
                    dict(original.get("parameters") or {}),
                ),
            )
            if intent is None:
                return DecisionReply(
                    "The old confirmation is set aside. Say the work order, action and corrected reason.",
                    decision,
                    "amended",
                )
            return self.present(
                intent,
                actor=actor,
                session_id=session_id,
                thread_id=thread_id,
                nonce=nonce,
                source_content=content,
                expected=decision,
            )
        if kind.non_consuming:
            if kind == Kind.DEFER:
                return DecisionReply(
                    "I will wait. The existing confirmation expiry still applies.",
                    decision,
                    "deferred",
                )
            if (
                kind.refreshes_lifetime
                and decision.playback_completed_at is not None
                and decision.review_turns < self.max_review_turns
            ):
                decision = self.advance(
                    decision,
                    expires_at=min(
                        self.now() + timedelta(seconds=self.ttl_s),
                        decision.armed_at + timedelta(seconds=self.max_armed_s),
                    ),
                    review_turns=decision.review_turns + 1,
                )
            if kind == Kind.HISTORY:
                return DecisionReply(
                    "This action is still a proposal. Nothing has been submitted for execution.",
                    decision,
                    "status",
                )
            return DecisionReply(decision.spoken_summary, decision, "review")
        reply = self.disarm(thread_id, "unrelated")
        return replace(reply, route_normally=True)

    def execute(self, decision, actor, phrase):
        """Claim the focus before recording submission and invoking the domain command."""
        from ai.core.decisions.receipts import finish_operation, start_operation
        from ai.core.tools.read_only import confirmed_write_exception
        from aichat.services.proposals import ProposalError

        decision = self.advance(decision, state=State.EXECUTING, execution_state="executing")
        operation, created = start_operation(decision)
        decision = self.advance(decision, operation_id=str(operation.pk))
        if not created:
            return self.history(decision, actor)
        try:
            with confirmed_write_exception():
                proposal = self.adapter.execute(decision, actor, phrase)
            finish_operation(operation, state="succeeded", receipt=proposal.receipt)
        except ProposalError as exc:
            finish_operation(operation, state="failed_before_effect", detail=str(exc))
        except Exception:
            finish_operation(operation, state="unknown")
        decision = self.advance(
            decision,
            state=State.RESOLVED,
            execution_state=operation.state,
            receipt_ref=operation.receipt_ref or None,
        )
        return self.history(decision, actor)

    def history(self, decision, actor):
        """Reconcile the durable result; never automatically execute again."""
        from ai.core.decisions.receipts import lookup_operation, spoken_receipt

        result = lookup_operation(
            actor=actor,
            thread_id=decision.thread_id,
            operation_id=decision.operation_id,
            decision_id=decision.decision_id,
        )
        if result is None:
            return DecisionReply(
                "The recorded result is unavailable in your current scope. No action was retried.",
                event="receipt_unavailable",
            )
        if result and (
            decision.execution_state != result["execution_state"]
            or decision.state == State.EXECUTING
            or decision.operation_id != result["operation_id"]
        ):
            decision = self.advance(
                decision,
                state=State.RESOLVED,
                execution_state=result["execution_state"],
                receipt_ref=result["receipt_ref"],
                operation_id=result["operation_id"],
            )
        return DecisionReply(spoken_receipt(result), decision, "receipt")

    def bind_playback(self, decision, *, utterance_id, spoken_text, spoken_hash):
        """Bind persisted text BEFORE dispatch; retries cannot move request time forward."""
        current = self.store.read(decision.thread_id)
        if current != decision or current.state != State.PRESENTED:
            raise DecisionConflict("The decision changed before playback.")
        if hashlib.sha256(spoken_text.encode()).hexdigest() != spoken_hash:
            raise DecisionConflict("The spoken content does not match its persisted hash.")
        if current.utterance_id == str(utterance_id):
            return current
        if (current.executable or {}).get("adapter") == "capture_review":
            from ai.core.decisions.adapters.capture_review import bind

            bind(current, utterance_id, spoken_text)
        if (current.executable or {}).get("adapter") == "approval":
            if self.approvals is None:
                raise DecisionConflict("Voice approvals are disabled.")
            self.approvals.bind_delivery(current, utterance_id)
        return self.advance(
            current,
            utterance_id=str(utterance_id),
            spoken_summary=spoken_text,
            spoken_summary_hash=spoken_hash,
            playback_requested_at=self.now(),
            playback_started_at=None,
            delivery_state=Delivery.REQUESTED,
        )

    def playback(self, decision, *, event, sequence, utterance_id, spoken_hash):
        """Strict monotonic callbacks; client completion is delivery only."""
        if (
            decision.state != State.PRESENTED
            or sequence != decision.sequence
            or str(utterance_id) != decision.utterance_id
            or spoken_hash != decision.spoken_summary_hash
            or decision.playback_requested_at is None
            or self.now() >= decision.expires_at
        ):
            raise DecisionConflict("Playback callback does not match the active decision.")
        now = self.now()
        if event == "playback-started" and decision.delivery_state == Delivery.REQUESTED:
            return self.advance(decision, delivery_state=Delivery.PLAYING, playback_started_at=now)
        if (
            event == "playback-completed"
            and decision.delivery_state == Delivery.PLAYING
            and decision.playback_started_at
        ):
            # Reject impossible immediate completion. A plausible callback can
            # start the lifetime but never shrink the conservative echo guard.
            if (now - decision.playback_started_at).total_seconds() < 0.25 or (
                now - decision.playback_requested_at
            ).total_seconds() < minimum_playback_seconds(decision.spoken_summary):
                raise DecisionConflict("Playback completion arrived before playback could finish.")
            changes = {"delivery_state": Delivery.DONE}
            if decision.playback_completed_at is None:
                changes.update(
                    playback_completed_at=now,
                    expires_at=min(
                        now + timedelta(seconds=self.ttl_s),
                        decision.armed_at + timedelta(seconds=self.max_armed_s),
                    ),
                )
            return self.advance(decision, **changes)
        raise DecisionConflict("Playback transition is stale or out of order.")


def get_coordinator():
    """Production must use shared Redis, even for multiple workers on one replica."""
    from ai.core.config import get_settings
    from ai.core.decisions.adapters.proposal_adapter import ProposalAdapter
    from ai.core.decisions.store import CachedPendingDecisionStore
    from django.conf import settings as django_settings

    config = get_settings()
    if not (
        config.feature_voice_decision_coordinator
        and config.feature_voice_write_confirmation
        and config.feature_voice_live
    ):
        raise DecisionStoreUnavailable("Voice decisions are disabled.")
    backend = django_settings.CACHES.get("default", {}).get("BACKEND", "").lower()
    if "redis" not in backend:
        raise DecisionStoreUnavailable("Voice decisions require the shared Redis cache.")
    approvals = None
    if config.feature_voice_approvals:
        from ai.core.decisions.adapters.approval_adapter import ApprovalAdapter
        from approvals.access import scoped_inbox_enabled
        from approvals.review_evidence import revision_binding_enabled

        if not scoped_inbox_enabled() or not revision_binding_enabled():
            raise DecisionStoreUnavailable(
                "Voice approvals require scoped, revision-bound screen review."
            )
        approvals = ApprovalAdapter()
    return DecisionCoordinator(
        store=CachedPendingDecisionStore(),
        adapter=ProposalAdapter(),
        approval_adapter=approvals,
        ttl_s=config.voice_decision_ttl_s,
        max_review_turns=config.voice_decision_max_review_turns,
        max_armed_s=config.voice_decision_max_armed_s,
    )
