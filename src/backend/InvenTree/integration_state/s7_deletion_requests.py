"""Private deletion-impact decisions and explicitly synthetic copy progress.

No public request/approval endpoint, model, repository, purge, TTL or external
adapter exists here. Source/retention claims are inputs for review, not proof.
Durable native authorization and complete impact coverage remain separate.
"""

import hashlib
import json
from dataclasses import dataclass, field, replace
from uuid import UUID

from assets.integration_contracts import ClientSourceIdentity
from integration_state.s7_contracts import (
    SourceAspectIdentity,
    exact_epoch,
    literal,
    positive_id,
)

MAX_IMPACTS = 1000
MAX_ATTEMPTS = 1000


def _source(value):
    if type(value) is not SourceAspectIdentity:
        raise ValueError('An exact source aspect identity is required')
    value.__post_init__()


def _client(value):
    if type(value) is not ClientSourceIdentity:
        raise ValueError('An exact native client identity is required')
    value.__post_init__()


def _revision(value):
    if value is None:
        return
    if type(value) is str:
        literal(value, 256)
    else:
        positive_id(value)


def _digest(value):
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in '0123456789abcdef' for c in value)
    ):
        raise ValueError('An exact synthetic evidence digest is required')


@dataclass(frozen=True, kw_only=True)
class DeletionIntent:
    """An actor/client-bound private request value, never a mutation grant."""

    request_id: UUID
    request_key: str = field(repr=False)
    request_revision: int
    actor_key: str = field(repr=False)
    client: ClientSourceIdentity = field(repr=False)
    scope: tuple[SourceAspectIdentity, ...] = field(repr=False)
    default_retention: str = field(default='indefinite', init=False)

    def __post_init__(self):
        """Keep exact scope and indefinite default retention without new privilege."""
        exact_epoch(self.request_id)
        literal(self.request_key, 128)
        positive_id(self.request_revision)
        literal(self.actor_key, 128)
        _client(self.client)
        if type(self.scope) is not tuple or not 1 <= len(self.scope) <= MAX_IMPACTS:
            raise ValueError('A bounded explicit request scope is required')
        for source in self.scope:
            _source(source)
            if source.environment != self.client.environment:
                raise ValueError('Request scope cannot cross source environments')
        if len(set(self.scope)) != len(self.scope):
            raise ValueError('Request scope repeats a source aspect')
        if (
            type(self.default_retention) is not str
            or self.default_retention != 'indefinite'
        ):
            raise ValueError('No retention exception is authorized by this request')


def _intent(value):
    if type(value) is not DeletionIntent:
        raise ValueError('An exact private deletion intent is required')
    value.__post_init__()


def intent_fingerprint(intent):
    """Bind declared private payload bytes, not authorization or source truth."""
    _intent(intent)
    payload = {
        'request_id': str(intent.request_id),
        'request_key': intent.request_key,
        'request_revision': intent.request_revision,
        'actor_key': intent.actor_key,
        'client': [intent.client.environment, intent.client.pk],
        'scope': [
            [s.environment, str(s.epoch), s.entity_kind, s.aspect, s.source_id]
            for s in intent.scope
        ],
        'default_retention': intent.default_retention,
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')
        ).encode('utf-8')
    ).hexdigest()


def same_intent(previous, incoming):
    """Refuse actor/client or payload adoption on an apparent idempotent retry."""
    _intent(previous)
    _intent(incoming)
    if previous.actor_key != incoming.actor_key or previous.client != incoming.client:
        raise PermissionError('The prior request belongs to another actor or client')
    if previous.request_key != incoming.request_key or intent_fingerprint(
        previous
    ) != intent_fingerprint(incoming):
        raise ValueError('The request identity has a conflicting payload or revision')
    return True


def _permitted(intent, authorize, fingerprint):
    if not callable(authorize):
        raise ValueError('A current native-owner authorizer is required')
    if (
        intent_fingerprint(intent) != fingerprint
        or authorize(intent) is not True
        or intent_fingerprint(intent) != fingerprint
    ):
        raise PermissionError('Current request authority is unavailable')


@dataclass(frozen=True, kw_only=True)
class CopyImpact:
    """One privately supplied retained-copy claim, not historical attribution proof."""

    copy_key: str = field(repr=False)
    source: SourceAspectIdentity
    copy_revision: int | str | None
    owners: tuple[ClientSourceIdentity, ...] | None = field(repr=False)
    attribution: str = 'unknown'
    retention: str = 'indefinite'

    def __post_init__(self):
        """Keep unknown attribution/revisions and independent owners explicit."""
        literal(self.copy_key, 256)
        _source(self.source)
        _revision(self.copy_revision)
        if self.owners is not None:
            if type(self.owners) is not tuple or len(self.owners) > MAX_IMPACTS:
                raise ValueError('An exact bounded owner tuple is required')
            for owner in self.owners:
                _client(owner)
                if owner.environment != self.source.environment:
                    raise ValueError('Copy owners cannot cross source environments')
            if len(set(self.owners)) != len(self.owners):
                raise ValueError('Copy ownership repeats a client')
        if type(self.attribution) is not str or self.attribution not in (
            'unknown',
            'source_claimed',
        ):
            raise ValueError('An explicit producer attribution claim is required')
        if type(self.retention) is not str or self.retention not in (
            'indefinite',
            'protected',
            'unknown',
            'reviewable',
        ):
            raise ValueError('An explicit retention disposition is required')


def _impact(value):
    if type(value) is not CopyImpact:
        raise ValueError('An exact private copy-impact claim is required')
    value.__post_init__()


def _impact_fingerprint(impact):
    """Bind copy ownership/retention independently of its content revision."""
    _impact(impact)
    source = impact.source
    payload = {
        'copy_key': impact.copy_key,
        'copy_revision': impact.copy_revision,
        'source': [
            source.environment,
            str(source.epoch),
            source.entity_kind,
            source.aspect,
            source.source_id,
        ],
        'owners': None
        if impact.owners is None
        else [[owner.environment, owner.pk] for owner in impact.owners],
        'attribution': impact.attribution,
        'retention': impact.retention,
    }
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')
        ).encode('utf-8')
    ).hexdigest()


def _disposition(intent, impact):
    _impact(impact)
    if impact.source not in intent.scope:
        return 'out_of_scope'
    if impact.owners is None or impact.attribution == 'unknown':
        return 'unresolved'
    if intent.client not in impact.owners:
        return 'out_of_scope'
    if len(impact.owners) > 1:
        return 'shared_retained'
    if impact.retention in ('indefinite', 'protected'):
        return 'policy_retained'
    if impact.copy_revision is None or impact.retention == 'unknown':
        return 'unresolved'
    return 'review_required'


@dataclass(frozen=True, kw_only=True)
class CopyDecision:
    """Minimal private outcome; it exposes no other owner's coordinates."""

    copy_key: str = field(repr=False)
    disposition: str

    def __post_init__(self):
        """No copy decision denotes approval, completed erasure or a command."""
        literal(self.copy_key, 256)
        if type(self.disposition) is not str or self.disposition not in (
            'out_of_scope',
            'unresolved',
            'shared_retained',
            'policy_retained',
            'review_required',
        ):
            raise ValueError('An explicit non-actionable copy disposition is required')


@dataclass(frozen=True, kw_only=True)
class ReadinessAssessment:
    """Partial review material with coverage and native approval still unproved."""

    request: DeletionIntent = field(repr=False)
    decisions: tuple[CopyDecision, ...] = field(repr=False)
    coverage_complete: bool = field(default=False, init=False)
    native_authorized: bool = field(default=False, init=False)
    erasure_complete: bool = field(default=False, init=False)
    deletion_applied: bool = field(default=False, init=False)

    def __post_init__(self):
        """A constructor or empty list cannot certify all copies or authority."""
        _intent(self.request)
        if type(self.decisions) is not tuple or len(self.decisions) > MAX_IMPACTS:
            raise ValueError('An exact bounded copy-decision tuple is required')
        keys = set()
        for row in self.decisions:
            if type(row) is not CopyDecision:
                raise ValueError('An exact private copy decision is required')
            row.__post_init__()
            if row.copy_key in keys:
                raise ValueError('Copy decisions conflict or repeat')
            keys.add(row.copy_key)
        if any(
            value is not False
            for value in (
                self.coverage_complete,
                self.native_authorized,
                self.erasure_complete,
                self.deletion_applied,
            )
        ):
            raise ValueError(
                'Private review inputs do not qualify authority or erasure'
            )

    @property
    def state(self):
        """Even all-reviewable input claims still need qualified native review."""
        return (
            'needs_native_review'
            if self.decisions
            and all(r.disposition == 'review_required' for r in self.decisions)
            else 'blocked'
        )


def assess_readiness(intent, impacts, *, authorize):
    """Classify supplied claims without a new deletion policy or copy inventory."""
    fingerprint = intent_fingerprint(intent)
    _permitted(intent, authorize, fingerprint)
    if type(impacts) is not tuple or len(impacts) > MAX_IMPACTS:
        raise ValueError('A bounded exact private impact tuple is required')
    for impact in impacts:
        _impact(impact)
    result = ReadinessAssessment(
        request=intent,
        decisions=tuple(
            CopyDecision(
                copy_key=impact.copy_key, disposition=_disposition(intent, impact)
            )
            for impact in impacts
        ),
    )
    result.__post_init__()
    _permitted(intent, authorize, fingerprint)
    return result


@dataclass(frozen=True, kw_only=True)
class CopyProgress:
    """A bounded synthetic checkpoint; it is neither durable nor actual erasure."""

    request_fingerprint: str = field(repr=False)
    copy_fingerprint: str = field(repr=False)
    copy_key: str = field(repr=False)
    copy_revision: int | str | None
    attempt_id: UUID
    attempt_history: tuple[UUID, ...] = field(repr=False)
    max_attempts: int
    sequence: int
    status: str
    evidence_digest: str | None = field(default=None, repr=False)
    synthetic_only: bool = field(default=True, init=False)
    deletion_applied: bool = field(default=False, init=False)
    erasure_complete: bool = field(default=False, init=False)

    def __post_init__(self):
        """Replayed or forged markers cannot acquire a real deletion meaning."""
        _digest(self.request_fingerprint)
        _digest(self.copy_fingerprint)
        literal(self.copy_key, 256)
        _revision(self.copy_revision)
        exact_epoch(self.attempt_id)
        positive_id(self.max_attempts)
        if (
            self.max_attempts > MAX_ATTEMPTS
            or type(self.attempt_history) is not tuple
            or not 1 <= len(self.attempt_history) <= self.max_attempts
        ):
            raise ValueError('A bounded retained attempt history is required')
        for attempt in self.attempt_history:
            exact_epoch(attempt)
        if self.attempt_history[-1] != self.attempt_id or len(
            set(self.attempt_history)
        ) != len(self.attempt_history):
            raise ValueError(
                'The current attempt cannot reuse another attempt identity'
            )
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError('An exact nonnegative progress sequence is required')
        if type(self.status) is not str or self.status not in (
            'planned',
            'pending',
            'synthetic_verified',
            'failed',
            'uncertain',
            'retained',
        ):
            raise ValueError('An explicit synthetic progress status is required')
        if self.copy_revision is None and self.status not in ('uncertain', 'retained'):
            raise ValueError('Unqualified copy revision cannot advance progress')
        if self.evidence_digest is not None:
            _digest(self.evidence_digest)
        if self.status == 'synthetic_verified' and (
            self.evidence_digest is None or self.sequence == 0
        ):
            raise ValueError(
                'Synthetic verification requires an observed attempt and fixture evidence'
            )
        if (
            self.synthetic_only is not True
            or self.deletion_applied is not False
            or self.erasure_complete is not False
        ):
            raise ValueError('Synthetic progress cannot claim real deletion or erasure')

    @property
    def attempts_used(self):
        """Retained attempts cannot be reset by an ordinary retry transition."""
        return len(self.attempt_history)


def _progress(intent, impact, progress):
    if type(progress) is not CopyProgress:
        raise ValueError('An exact synthetic copy checkpoint is required')
    progress.__post_init__()
    _impact(impact)
    if (
        progress.request_fingerprint != intent_fingerprint(intent)
        or progress.copy_fingerprint != _impact_fingerprint(impact)
        or progress.copy_key != impact.copy_key
        or progress.copy_revision != impact.copy_revision
    ):
        raise ValueError('Progress belongs to another request or copy revision')


def start_copy_progress(intent, impact, *, attempt, max_attempts, authorize):
    """Begin a private fixture attempt, never submit an actual delete."""
    fingerprint = intent_fingerprint(intent)
    _permitted(intent, authorize, fingerprint)
    disposition = _disposition(intent, impact)
    if disposition == 'out_of_scope':
        raise PermissionError('This copy is outside the request scope')
    status = (
        'planned'
        if disposition == 'review_required'
        else ('uncertain' if disposition == 'unresolved' else 'retained')
    )
    result = CopyProgress(
        request_fingerprint=fingerprint,
        copy_fingerprint=_impact_fingerprint(impact),
        copy_key=impact.copy_key,
        copy_revision=impact.copy_revision,
        attempt_id=attempt,
        attempt_history=(attempt,),
        max_attempts=max_attempts,
        sequence=0,
        status=status,
    )
    _permitted(intent, authorize, fingerprint)
    return result


def transition_copy_progress(intent, impact, previous, incoming, *, authorize):
    """Fence exact request/copy/attempt/sequence before a synthetic transition."""
    fingerprint = intent_fingerprint(intent)
    _permitted(intent, authorize, fingerprint)
    for progress in (previous, incoming):
        _progress(intent, impact, progress)
    if (
        previous.attempt_id != incoming.attempt_id
        or previous.attempt_history != incoming.attempt_history
        or previous.max_attempts != incoming.max_attempts
    ):
        raise ValueError('Progress cannot adopt another attempt or reset its budget')
    if incoming.sequence < previous.sequence:
        result = previous
    elif incoming.sequence == previous.sequence:
        if incoming != previous:
            raise ValueError('A repeated progress sequence has a conflicting payload')
        result = previous
    else:
        allowed = {
            'planned': ('pending', 'uncertain', 'retained'),
            'pending': ('synthetic_verified', 'failed', 'uncertain', 'retained'),
        }
        if (
            incoming.sequence != previous.sequence + 1
            or incoming.status not in allowed.get(previous.status, ())
        ):
            raise ValueError('Progress is reordered or has no admitted transition')
        if _disposition(intent, impact) != 'review_required':
            raise ValueError('A shared, protected or unknown copy cannot advance')
        result = incoming
    _permitted(intent, authorize, fingerprint)
    return result


def retry_copy_progress(intent, impact, previous, *, attempt, authorize):
    """Keep uncertainty and attempt history; never reset an old request budget."""
    fingerprint = intent_fingerprint(intent)
    _permitted(intent, authorize, fingerprint)
    _progress(intent, impact, previous)
    exact_epoch(attempt)
    if (
        previous.status not in ('failed', 'uncertain')
        or previous.attempts_used >= previous.max_attempts
        or attempt in previous.attempt_history
    ):
        raise ValueError('This failed/uncertain attempt has no admitted retry')
    if _disposition(intent, impact) != 'review_required':
        raise ValueError('A shared, protected or unknown copy cannot be retried')
    result = replace(
        previous,
        attempt_id=attempt,
        attempt_history=(*previous.attempt_history, attempt),
        sequence=0,
        status='planned',
        evidence_digest=None,
    )
    _permitted(intent, authorize, fingerprint)
    return result
