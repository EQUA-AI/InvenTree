"""Transactional verification command services.

Every mutating command authenticates, resolves scope before disclosure,
requires permission and expected state/revision, binds a stable idempotency
key to its canonical payload, locks the session row, appends a typed event,
and records a replay-safe command ledger row (spec section 12).

Deterministic policy decides eligibility and blockers; an authorized human
owns confirmation and no-safe-match. AI participates nowhere in this module.
"""

from datetime import timedelta
from datetime import timezone as datetime_timezone

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from part.verification import policy as policy_module
from part.verification import scope as scope_module
from part.verification.availability import availability_snapshot
from part.verification.compatibility import evaluate_candidate
from part.verification.errors import (
    VerificationCandidateIneligible,
    VerificationCandidateStale,
    VerificationCommandError,
    VerificationContextInvalid,
    VerificationDisabled,
    VerificationIdempotencyConflict,
    VerificationNoSafeMatchInvalid,
    VerificationNotFound,
    VerificationPolicyUnavailable,
    VerificationRevalidationIndeterminate,
    VerificationRevisionConflict,
    VerificationScopeError,
    VerificationSessionExpired,
    VerificationSessionStale,
    VerificationStateConflict,
    VerificationUseError,
)
from part.verification.permissions import (
    PERM_ADD,
    PERM_CHANGE,
    PERM_CONFIRM,
    PERM_INVALIDATE,
    PERM_REVIEW,
    require_permission,
)
from part.verification.ranking import RankInput, compute_rank_factors, survivor_sort_key
from part.verification.requirements import build_requirements
from part.verification.retrieval import retrieve_candidates
from part.verification.revalidation import classify
from part.verification.schema import (
    CANCELLABLE_STATES,
    BlockerCodes,
    CommandCodes,
    ConsumerCodes,
    DecisionKind,
    DifferenceSeverity,
    EventType,
    EvidenceDecision,
    HashDomains,
    PartVerificationPurpose,
    PartVerificationState,
    PolicyStatus,
    hash_canonical,
)
from part.verification.sources import (
    _jsonable,
    accepted_evidence,
    build_source_observation,
    fingerprint,
    snapshot_candidate,
    source_fingerprint_for_session,
)

# Session states in which evidence may still be collected
_COLLECTING_STATES = frozenset({
    PartVerificationState.COLLECTING,
    PartVerificationState.EVALUATING,
    PartVerificationState.REVIEW_REQUIRED,
})

# Evaluate is legal from these states (spec section 6.2)
_EVALUATABLE_STATES = frozenset({
    PartVerificationState.COLLECTING,
    PartVerificationState.REVIEW_REQUIRED,
})


def _is_past(moment) -> bool:
    """Return True if a stored datetime is at or before the current time.

    Handles naive/aware mismatches: with USE_TZ disabled (test mode) the
    PostgreSQL ``timestamptz`` columns still return timezone-aware values, while
    ``timezone.now()`` is naive. Normalize both to the same awareness before
    comparing so the check is correct on every database backend.
    """
    if moment is None:
        return False

    now = timezone.now()
    if timezone.is_aware(moment) and timezone.is_naive(now):
        moment = timezone.make_naive(moment, datetime_timezone.utc)
    elif timezone.is_naive(moment) and timezone.is_aware(now):
        moment = timezone.make_aware(moment, datetime_timezone.utc)
    return moment <= now


def _require_enabled(flag: str | None = None):
    """Fail closed unless the feature (and optional capability flag) is on."""
    if not getattr(settings, 'AIMMS_RPF_ENABLED', False):
        raise VerificationDisabled('Right-Part Finder is disabled')
    if flag is not None and not getattr(settings, flag, False):
        raise VerificationDisabled(f'Verification capability is disabled: {flag}')


def _map_scope_error(error: Exception) -> VerificationScopeError:
    """Translate a scope resolution failure into a stable command error."""
    code = CommandCodes.RPF_SCOPE_MISMATCH
    if 'unresolved' in str(error) or 'authenticated' in str(error):
        code = CommandCodes.RPF_SCOPE_UNRESOLVED
    return VerificationScopeError(str(error), code=code)


def _session_scope(session) -> scope_module.VerificationScope:
    """Return the resolved scope stored on a session."""
    return scope_module.VerificationScope(
        customer_id=session.scope_customer_id, site_key=session.scope_site_key or None
    )


def _require_session_scope(actor, session):
    """Reauthorize the actor against the session's stored scope."""
    try:
        scope_module.require_scope(actor, _session_scope(session))
    except scope_module.VerificationScopeError as error:
        raise _map_scope_error(error) from error


def _locked_session(session_id):
    """Load and lock a session row, failing scope-safely when absent."""
    from part.verification_models import PartVerificationSession

    session = (
        # Lock only the session row itself; the nullable context relations
        # are joined for reading and cannot take FOR UPDATE on Postgres.
        PartVerificationSession.objects
        .select_for_update(of=('self',))
        .select_related(
            'policy',
            'requested_part',
            'machine',
            'machine_part',
            'bom_item',
            'job_kit_line',
        )
        .filter(pk=session_id)
        .first()
    )
    if session is None:
        raise VerificationNotFound('Verification session not found')
    return session


def _begin_command(
    *,
    command: str,
    idempotency_key: str,
    payload: dict,
    actor,
    session=None,
    scope_fingerprint: str = '',
):
    """Begin or replay one command under its idempotency key.

    Returns ``(ledger_row, replayed)``. An exact completed replay returns the
    stored row with ``replayed=True``; a same-key different-payload call
    raises a stable conflict.
    """
    from part.verification_models import PartVerificationCommand

    if not idempotency_key:
        raise VerificationContextInvalid('An idempotency key is required')

    request_hash = hash_canonical(HashDomains.COMMAND, payload)

    existing = (
        PartVerificationCommand.objects
        .select_for_update()
        .filter(command=command, idempotency_key=idempotency_key)
        .first()
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise VerificationIdempotencyConflict(
                'The idempotency key was reused with a different payload'
            )
        if existing.status == PartVerificationCommand.STATUS_COMPLETED:
            return existing, True
        raise VerificationIdempotencyConflict(
            'A command with this idempotency key is still in flight'
        )

    try:
        with transaction.atomic():
            row = PartVerificationCommand.objects.create(
                command=command,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                actor=actor if getattr(actor, 'pk', None) else None,
                session=session,
                scope_fingerprint=scope_fingerprint,
            )
    except IntegrityError as error:
        raise VerificationIdempotencyConflict(
            'A concurrent command holds this idempotency key'
        ) from error

    return row, False


def _complete_command(row, result: dict):
    """Mark a command ledger row completed with its canonical result."""
    from part.verification_models import PartVerificationCommand

    row.status = PartVerificationCommand.STATUS_COMPLETED
    row.result = result
    row.completed_at = timezone.now()
    row.save(update_fields=['status', 'result', 'completed_at'])
    return result


def _append_event(
    session, event_type, *, actor=None, reason='', correlation_id='', metadata=None
):
    """Append one typed event; event failure rolls back the command."""
    from part.verification_models import PartVerificationEvent

    PartVerificationEvent.objects.create(
        session=session,
        event_type=event_type,
        state=session.state,
        reason=reason[:64],
        actor=actor if getattr(actor, 'pk', None) else None,
        correlation_id=correlation_id[:36],
        metadata=metadata or {},
    )


def _check_revision(session, expected_revision):
    """Require the expected session revision."""
    if expected_revision is None or int(expected_revision) != session.revision:
        raise VerificationRevisionConflict(
            f'Expected revision {expected_revision}, current is {session.revision}'
        )


class _StaleDetected(Exception):  # noqa: N818 - internal control-flow signal
    """Internal control flow: staleness found mid-command.

    Raised to roll back the failing command's writes before the stale
    projection is persisted in its own transaction.
    """

    def __init__(
        self,
        reason: str,
        error: VerificationCommandError,
        *,
        session_id=None,
        metadata=None,
    ):
        """Store the stale reason and the public error to re-raise."""
        super().__init__(reason)
        self.reason = reason
        self.error = error
        self.session_id = session_id
        self.metadata = metadata or {}


def _apply_stale(session_id, reason: str, *, actor=None, metadata=None):
    """Persist STALE in an independent transaction.

    Runs after the failing command rolled back, so the stale projection
    survives the command failure (spec section 14.2). When nested inside a
    consumer's outer transaction this is a savepoint and shares the outer
    fate; the fail-closed blocking result holds regardless.
    """
    from part.verification_models import PartVerificationSession

    with transaction.atomic():
        session = (
            PartVerificationSession.objects
            .select_for_update(of=('self',))
            .filter(pk=session_id)
            .first()
        )
        if session is not None and session.state != PartVerificationState.STALE:
            _mark_stale(session, reason, actor=actor, metadata=metadata)


def _mark_stale(session, reason: str, *, actor=None, metadata=None):
    """Transition a session to STALE with a typed reason, appending an event."""
    session.state = PartVerificationState.STALE
    session.stale_reason = reason[:64]
    session.save(update_fields=['state', 'stale_reason', 'updated_at'])
    _append_event(
        session,
        EventType.SESSION_STALE,
        actor=actor,
        reason=reason,
        metadata=metadata or {},
    )


def _load_context(model, pk, label):
    """Load one context object by pk, failing scope-safely when absent."""
    if pk is None:
        return None
    instance = model.objects.filter(pk=pk).first()
    if instance is None:
        raise VerificationNotFound(f'Unknown {label}')
    return instance


@transaction.atomic
def create_session(
    *,
    purpose: str,
    actor,
    idempotency_key: str,
    requested_part_id=None,
    machine_id=None,
    machine_part_id=None,
    bom_item_id=None,
    work_order_id=None,
    job_kit_line_id=None,
    correlation_id: str = '',
):
    """Create one scoped verification session for one precise purpose."""
    from tasks.jobkit_models import JobKitLine
    from tasks.models import WorkOrder

    from assets.models import AssetMachine, MachinePart
    from part.models import BomItem, Part
    from part.verification_models import PartVerificationSession

    _require_enabled('AIMMS_RPF_COLLECTION_ENABLED')
    require_permission(actor, PERM_ADD)

    if purpose not in PartVerificationPurpose.values:
        raise VerificationContextInvalid(f'Unsupported purpose: {purpose}')

    machine = _load_context(AssetMachine, machine_id, 'machine')
    machine_part = _load_context(MachinePart, machine_part_id, 'installed part row')
    bom_item = _load_context(BomItem, bom_item_id, 'BOM line')
    work_order = _load_context(WorkOrder, work_order_id, 'work order')
    job_kit_line = _load_context(JobKitLine, job_kit_line_id, 'Job Kit line')
    requested_part = _load_context(Part, requested_part_id, 'part')

    # Derive implied context: an exact installed row or BOM line fixes both
    # the machine and the requested identity.
    if machine_part is not None:
        if machine is not None and machine.pk != machine_part.machine_id:
            raise VerificationContextInvalid(
                'The installed-part row does not belong to the given machine'
            )
        machine = machine or machine_part.machine
        requested_part = requested_part or machine_part.part
    if bom_item is not None and requested_part is None:
        requested_part = bom_item.sub_part
    if job_kit_line is not None and requested_part is None:
        requested_part = job_kit_line.requested_part

    try:
        target = scope_module.scope_for_context(machine=machine, work_order=work_order)
        scope_module.require_scope(actor, target)
    except scope_module.VerificationScopeError as error:
        mapped = _map_scope_error(error)
        if mapped.code == CommandCodes.RPF_SCOPE_MISMATCH:
            # Scope-safe 404 (spec 17.3 rule 7): an out-of-scope context must
            # be indistinguishable from an absent one, or session creation
            # becomes an existence oracle for hidden customer assets.
            raise VerificationNotFound('Unknown verification context') from error
        raise mapped from error

    try:
        policy = policy_module.load_active_policy()
    except policy_module.PolicyError as error:
        raise VerificationPolicyUnavailable(str(error)) from error

    payload = {
        'purpose': purpose,
        'requested_part': requested_part.pk if requested_part else None,
        'machine': machine.pk if machine else None,
        'machine_part': machine_part.pk if machine_part else None,
        'bom_item': bom_item.pk if bom_item else None,
        'work_order': work_order.pk if work_order else None,
        'job_kit_line': job_kit_line.pk if job_kit_line else None,
    }
    scope_fp = scope_module.scope_fingerprint(target)

    row, replayed = _begin_command(
        command='create_session',
        idempotency_key=idempotency_key,
        payload=payload,
        actor=actor,
        scope_fingerprint=scope_fp,
    )
    if replayed:
        return PartVerificationSession.objects.get(pk=row.result['session_id'])

    session = PartVerificationSession.objects.create(
        purpose=purpose,
        state=PartVerificationState.COLLECTING,
        scope_customer_id=target.customer_id,
        scope_site_key=target.site_key or '',
        scope_fingerprint=scope_fp,
        requested_part=requested_part,
        machine=machine,
        machine_part=machine_part,
        bom_item=bom_item,
        work_order=work_order,
        job_kit_line=job_kit_line,
        policy=policy,
        created_by=actor if getattr(actor, 'pk', None) else None,
    )
    row.session = session
    row.save(update_fields=['session'])

    _append_event(
        session,
        EventType.SESSION_CREATED,
        actor=actor,
        correlation_id=correlation_id,
        metadata={'purpose': purpose},
    )
    _complete_command(row, {'session_id': session.pk, 'reference': session.reference})
    return session


@transaction.atomic
def attach_evidence(
    *,
    session_id,
    actor,
    idempotency_key: str,
    requirement_key: str,
    value=None,
    unit: str = '',
    source_kind: str = 'observation',
    digest: str = '',
    expires_at=None,
    correlation_id: str = '',
):
    """Attach one proposed evidence item to a session.

    The item starts PROPOSED; only an authorized acceptance makes it usable
    as a requirement fact. Extracted or free-text content is stored as data,
    never interpreted as instructions.
    """
    from part.verification_models import PartVerificationEvidence

    _require_enabled('AIMMS_RPF_COLLECTION_ENABLED')
    require_permission(actor, PERM_CHANGE)

    session = _locked_session(session_id)
    _require_session_scope(actor, session)

    if session.state not in _COLLECTING_STATES:
        raise VerificationStateConflict(
            f'Evidence cannot be attached in state {session.state}'
        )

    payload = {
        'session': session.pk,
        'requirement_key': requirement_key,
        # Deep-coerce floats from client JSON so canonical hashing never
        # meets a binary float, including inside nested range/set values.
        'value': _jsonable(value),
        'unit': unit,
        'source_kind': source_kind,
        'digest': digest,
    }
    row, replayed = _begin_command(
        command='attach_evidence',
        idempotency_key=idempotency_key,
        payload=payload,
        actor=actor,
        session=session,
        scope_fingerprint=session.scope_fingerprint,
    )
    if replayed:
        return PartVerificationEvidence.objects.get(pk=row.result['evidence_id'])

    evidence = PartVerificationEvidence.objects.create(
        session=session,
        requirement_key=requirement_key,
        source_kind=source_kind,
        raw_value=payload['value'],
        unit=unit,
        digest=digest,
        origin='human',
        expires_at=expires_at,
        source_fingerprint=hash_canonical(
            HashDomains.EVIDENCE,
            {'key': requirement_key, 'value': payload['value'], 'unit': unit},
        ),
        created_by=actor if getattr(actor, 'pk', None) else None,
    )

    _append_event(
        session,
        EventType.EVIDENCE_ATTACHED,
        actor=actor,
        correlation_id=correlation_id,
        metadata={'evidence_id': evidence.pk, 'requirement_key': requirement_key},
    )
    _complete_command(row, {'evidence_id': evidence.pk})
    return evidence


@transaction.atomic
def decide_evidence(
    *,
    session_id,
    evidence_id,
    actor,
    idempotency_key: str,
    accept: bool,
    reason: str = '',
    correlation_id: str = '',
):
    """Accept or reject one proposed evidence item under policy."""
    _require_enabled('AIMMS_RPF_COLLECTION_ENABLED')
    require_permission(actor, PERM_REVIEW)

    session = _locked_session(session_id)
    _require_session_scope(actor, session)

    evidence = session.evidence_items.select_for_update().filter(pk=evidence_id).first()
    if evidence is None:
        raise VerificationNotFound('Evidence item not found')

    if evidence.decision != EvidenceDecision.PROPOSED:
        raise VerificationStateConflict(
            f'Evidence is already {evidence.decision} and cannot be re-decided'
        )

    payload = {'session': session.pk, 'evidence': evidence.pk, 'accept': accept}
    row, replayed = _begin_command(
        command='decide_evidence',
        idempotency_key=idempotency_key,
        payload=payload,
        actor=actor,
        session=session,
        scope_fingerprint=session.scope_fingerprint,
    )
    if replayed:
        return evidence

    evidence.decision = (
        EvidenceDecision.ACCEPTED if accept else EvidenceDecision.REJECTED
    )
    evidence.decided_by = actor if getattr(actor, 'pk', None) else None
    evidence.decided_at = timezone.now()
    evidence.save(update_fields=['decision', 'decided_by', 'decided_at'])

    _append_event(
        session,
        EventType.EVIDENCE_DECIDED,
        actor=actor,
        reason=reason,
        correlation_id=correlation_id,
        metadata={'evidence_id': evidence.pk, 'decision': evidence.decision},
    )
    _complete_command(row, {'evidence_id': evidence.pk, 'decision': evidence.decision})
    return evidence


def _check_policy_current(session):
    """Require the session's bound policy to be active and hash-consistent."""
    policy = session.policy
    if policy.status != PolicyStatus.ACTIVE:
        code = (
            BlockerCodes.POLICY_REVOKED
            if policy.status == PolicyStatus.REVOKED
            else BlockerCodes.POLICY_UNAVAILABLE
        )
        raise VerificationPolicyUnavailable(
            f'Bound policy is not active ({policy.status})', code=code
        )
    if policy.definition_hash != policy_module.policy_hash(policy.definition):
        raise VerificationPolicyUnavailable('Bound policy definition hash mismatch')
    return policy


def _run_evaluation(session, actor, correlation_id=''):
    """Run deterministic construction, retrieval, evaluation, and ranking.

    Called with the session row already locked. Returns the result summary.
    """
    from part.verification_models import PartCandidateEvaluation

    policy = _check_policy_current(session)
    session.state = PartVerificationState.EVALUATING

    build = build_requirements(session, policy)
    session.requirements_hash = build.requirements_hash

    if build.blocked:
        session.state = PartVerificationState.COLLECTING
        session.save()
        _append_event(
            session,
            EventType.EVALUATION_BLOCKED,
            actor=actor,
            correlation_id=correlation_id,
            metadata={'blockers': build.blockers},
        )
        return {
            'state': session.state,
            'revision': session.revision,
            'blockers': build.blockers,
        }

    max_candidates, tier_cap = policy_module.retrieval_limits(policy)
    retrieval = retrieve_candidates(
        session, max_candidates=max_candidates, tier_cap=tier_cap
    )

    specs = policy_module.policy_requirements(policy)
    requirements_by_key = {item['key']: item for item in build.requirements}
    evidence_has_expiry = any(
        item.expires_at is not None for item in accepted_evidence(session)
    )

    session.candidate_evaluations.filter(session_revision=session.revision).delete()

    now = timezone.now()
    survivors = []
    evaluation_hashes = []

    for entry in retrieval.entries:
        candidate = entry.part
        result = evaluate_candidate(candidate, specs, requirements_by_key)

        snapshot = snapshot_candidate(candidate)
        candidate_fp = fingerprint(snapshot)

        evaluation_hash = hash_canonical(
            HashDomains.EVALUATION,
            {
                'candidate': candidate.pk,
                'candidate_fingerprint': candidate_fp,
                'requirements_hash': build.requirements_hash,
                'policy_hash': policy.definition_hash,
                'eligible': result.eligible,
                'conflicts': result.hard_conflicts,
                'missing': result.missing_attributes,
                'matched': result.matched_attributes,
            },
        )
        evaluation_hashes.append(evaluation_hash)

        evaluation = PartCandidateEvaluation.objects.create(
            session=session,
            session_revision=session.revision,
            candidate=candidate,
            retrieval_tiers=entry.tiers,
            candidate_snapshot=snapshot,
            candidate_fingerprint=candidate_fp,
            eligible=result.eligible,
            hard_conflicts=result.hard_conflicts,
            matched_attributes=result.matched_attributes,
            missing_attributes=result.missing_attributes,
            availability_snapshot=availability_snapshot(candidate),
            requirements_hash=build.requirements_hash,
            policy=policy,
            evaluation_hash=evaluation_hash,
            evaluated_at=now,
        )

        if result.eligible:
            inputs = RankInput(
                candidate=candidate,
                tiers=entry.tiers,
                soft_matched=result.soft_matched,
                soft_considered=result.soft_considered,
                candidate_fact_count=result.candidate_fact_count,
                policy_attribute_count=len(specs),
                evidence_has_expiry=evidence_has_expiry,
            )
            inputs.session = session
            factors, rank_value = compute_rank_factors(
                inputs, policy_module.rank_factor_weights(policy)
            )
            survivors.append({
                'evaluation': evaluation,
                'rank_factors': factors,
                'rank_value': rank_value,
                'candidate_pk': candidate.pk,
            })

    survivors.sort(key=survivor_sort_key)
    for position, survivor in enumerate(survivors, start=1):
        evaluation = survivor['evaluation']
        evaluation.rank_factors = survivor['rank_factors']
        evaluation.rank_value = survivor['rank_value']
        evaluation.rank = position
        evaluation.save(update_fields=['rank_factors', 'rank_value', 'rank'])

    session.state = PartVerificationState.REVIEW_REQUIRED
    session.source_fingerprint = source_fingerprint_for_session(session)
    session.evaluation_hash = hash_canonical(
        HashDomains.EVALUATION, sorted(evaluation_hashes)
    )
    session.universe_complete = retrieval.universe_complete
    session.considered_count = retrieval.total_considered
    session.eligible_count = len(survivors)
    session.expires_at = now + timedelta(hours=policy_module.expiry_hours(policy))
    session.stale_reason = ''
    session.save()

    metadata = {
        'universe_complete': retrieval.universe_complete,
        'considered': retrieval.total_considered,
        'eligible': len(survivors),
    }
    if not retrieval.universe_complete:
        metadata['blocker'] = BlockerCodes.SEARCH_LIMIT_REACHED

    _append_event(
        session,
        EventType.SESSION_EVALUATED,
        actor=actor,
        correlation_id=correlation_id,
        metadata=metadata,
    )

    return {
        'state': session.state,
        'revision': session.revision,
        'blockers': [],
        **metadata,
    }


@transaction.atomic
def evaluate_session(
    *,
    session_id,
    actor,
    expected_revision,
    idempotency_key: str,
    correlation_id: str = '',
):
    """Deterministically build requirements and evaluate all candidates."""
    _require_enabled('AIMMS_RPF_EVALUATION_ENABLED')
    require_permission(actor, PERM_CHANGE)

    session = _locked_session(session_id)
    _require_session_scope(actor, session)
    # Replay resolves before state/revision guards (see confirm_candidate)
    payload = {
        'session': session.pk,
        'expected_revision': expected_revision,
        'command': 'evaluate',
    }
    row, replayed = _begin_command(
        command='evaluate_session',
        idempotency_key=idempotency_key,
        payload=payload,
        actor=actor,
        session=session,
        scope_fingerprint=session.scope_fingerprint,
    )
    if replayed:
        return row.result

    _check_revision(session, expected_revision)

    if session.state not in _EVALUATABLE_STATES:
        raise VerificationStateConflict(
            f'Evaluation is not permitted in state {session.state}'
        )

    result = _run_evaluation(session, actor, correlation_id)
    return _complete_command(row, result)


@transaction.atomic
def reevaluate_session(
    *,
    session_id,
    actor,
    expected_revision,
    idempotency_key: str,
    correlation_id: str = '',
):
    """Reopen a stale session as a new revision and evaluate it."""
    _require_enabled('AIMMS_RPF_EVALUATION_ENABLED')
    require_permission(actor, PERM_CHANGE)

    session = _locked_session(session_id)
    _require_session_scope(actor, session)
    # Replay resolves before state/revision guards (see confirm_candidate)
    payload = {
        'session': session.pk,
        'expected_revision': expected_revision,
        'command': 'reevaluate',
    }
    row, replayed = _begin_command(
        command='reevaluate_session',
        idempotency_key=idempotency_key,
        payload=payload,
        actor=actor,
        session=session,
        scope_fingerprint=session.scope_fingerprint,
    )
    if replayed:
        return row.result

    _check_revision(session, expected_revision)

    if session.state != PartVerificationState.STALE:
        raise VerificationStateConflict(
            f'Reevaluation is only permitted from STALE, not {session.state}'
        )

    session.revision += 1
    session.current_decision = None
    session.save(update_fields=['revision', 'current_decision', 'updated_at'])
    _append_event(
        session,
        EventType.SESSION_REEVALUATED,
        actor=actor,
        correlation_id=correlation_id,
        metadata={'revision': session.revision},
    )

    result = _run_evaluation(session, actor, correlation_id)
    return _complete_command(row, result)


@transaction.atomic
def reject_candidate(
    *,
    session_id,
    evaluation_id,
    actor,
    expected_revision,
    idempotency_key: str,
    reason: str,
    correlation_id: str = '',
):
    """Record a human rejection of one candidate without altering eligibility."""
    _require_enabled('AIMMS_RPF_EVALUATION_ENABLED')
    require_permission(actor, PERM_REVIEW)

    session = _locked_session(session_id)
    _require_session_scope(actor, session)
    _check_revision(session, expected_revision)

    if session.state != PartVerificationState.REVIEW_REQUIRED:
        raise VerificationStateConflict(
            f'Candidates cannot be rejected in state {session.state}'
        )

    if not reason:
        raise VerificationContextInvalid('A rejection reason is required')

    evaluation = (
        session.candidate_evaluations
        .select_for_update()
        .filter(pk=evaluation_id, session_revision=session.revision)
        .first()
    )
    if evaluation is None:
        raise VerificationNotFound('Candidate evaluation not found')

    payload = {'session': session.pk, 'evaluation': evaluation.pk, 'reason': reason}
    row, replayed = _begin_command(
        command='reject_candidate',
        idempotency_key=idempotency_key,
        payload=payload,
        actor=actor,
        session=session,
        scope_fingerprint=session.scope_fingerprint,
    )
    if replayed:
        return evaluation

    evaluation.rejected = True
    evaluation.rejected_reason = reason
    evaluation.rejected_by = actor if getattr(actor, 'pk', None) else None
    evaluation.save(update_fields=['rejected', 'rejected_reason', 'rejected_by'])

    _append_event(
        session,
        EventType.CANDIDATE_REJECTED,
        actor=actor,
        reason='human_rejection',
        correlation_id=correlation_id,
        metadata={'evaluation_id': evaluation.pk, 'candidate': evaluation.candidate_id},
    )
    _complete_command(row, {'evaluation_id': evaluation.pk})
    return evaluation


def _require_current_snapshot(session, actor):
    """Require that the reviewed snapshot still matches current facts.

    A changed source fingerprint or requirement set raises ``_StaleDetected``
    so the caller rolls back its writes, persists STALE independently, and
    re-raises the public error; mixed old/new facts are never confirmed.
    """
    current_fp = source_fingerprint_for_session(session)
    if current_fp != session.source_fingerprint:
        raise _StaleDetected(
            'source_changed',
            VerificationSessionStale('The verified facts changed after evaluation'),
        )

    build = build_requirements(session, session.policy)
    if build.blocked or build.requirements_hash != session.requirements_hash:
        raise _StaleDetected(
            'requirements_changed',
            VerificationSessionStale('The typed requirements changed after evaluation'),
        )
    return build


def _decision_snapshot(session, build, *, evaluation=None):
    """Build the immutable decision snapshot for a session decision."""
    return {
        'schema_version': 1,
        'session': {
            'id': session.pk,
            'reference': session.reference,
            'purpose': session.purpose,
            'revision': session.revision,
        },
        'scope_fingerprint': session.scope_fingerprint,
        'requirements_hash': session.requirements_hash,
        'evaluation_hash': session.evaluation_hash,
        'universe_complete': session.universe_complete,
        'observation': build_source_observation(session),
        'policy': {
            'key': session.policy.key,
            'version': session.policy.version,
            'hash': session.policy.definition_hash,
        },
        'selected': {
            'part': evaluation.candidate_id if evaluation else None,
            'evaluation': evaluation.pk if evaluation else None,
            'candidate_fingerprint': (
                evaluation.candidate_fingerprint if evaluation else None
            ),
            'evaluation_hash': evaluation.evaluation_hash if evaluation else None,
        },
        'requirements': [
            {
                'key': item['key'],
                'value_kind': item['value_kind'],
                'operator': item['operator'],
                'unit': item['unit'],
                'value': item['value'],
                'resolution': item['resolution'],
                'hard_constraint': item['hard_constraint'],
            }
            for item in build.requirements
        ],
    }


def _create_decision(session, actor, kind, snapshot, *, evaluation=None, reason=''):
    """Create the immutable decision row and set the current pointer."""
    from part.verification_models import PartVerificationDecision

    now = timezone.now()

    decision = PartVerificationDecision.objects.create(
        session=session,
        session_revision=session.revision,
        kind=kind,
        selected_evaluation=evaluation,
        selected_part_id=evaluation.candidate_id if evaluation else None,
        decision_snapshot=snapshot,
        decision_hash=hash_canonical(
            HashDomains.DECISION,
            {
                'session': session.pk,
                'revision': session.revision,
                'kind': str(kind),
                'snapshot': snapshot,
            },
        ),
        requirements_hash=session.requirements_hash,
        source_fingerprint=session.source_fingerprint,
        evaluation_hash=session.evaluation_hash,
        policy_hash=session.policy.definition_hash,
        scope_fingerprint=session.scope_fingerprint,
        policy=session.policy,
        decided_by=actor,
        reason=reason,
        decided_at=now,
        valid_until=now + timedelta(hours=policy_module.expiry_hours(session.policy)),
    )

    session.current_decision = decision
    return decision


def confirm_candidate(
    *,
    session_id,
    evaluation_id,
    actor,
    expected_revision,
    idempotency_key: str,
    reason: str,
    correlation_id: str = '',
):
    """Confirm one eligible candidate under locks and a current-state recheck.

    Staleness found during the locked recheck rolls the command back, then
    persists the STALE projection in its own transaction before re-raising.
    """
    try:
        with transaction.atomic():
            return _confirm_candidate_locked(
                session_id=session_id,
                evaluation_id=evaluation_id,
                actor=actor,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                reason=reason,
                correlation_id=correlation_id,
            )
    except _StaleDetected as stale:
        _apply_stale(session_id, stale.reason, actor=actor)
        raise stale.error from None


def _confirm_candidate_locked(
    *,
    session_id,
    evaluation_id,
    actor,
    expected_revision,
    idempotency_key,
    reason,
    correlation_id,
):
    """Locked confirmation body; see ``confirm_candidate``."""
    from part.models import Part

    _require_enabled('AIMMS_RPF_CONFIRMATION_ENABLED')
    require_permission(actor, PERM_CONFIRM)

    session = _locked_session(session_id)
    _require_session_scope(actor, session)

    # Replay is resolved before state/revision guards so an exact retry of a
    # completed confirmation returns the original decision even after the
    # session moved on (FR-RPF-030 / spec section 12.1).
    payload = {
        'session': session.pk,
        'evaluation': evaluation_id,
        'expected_revision': expected_revision,
        'command': 'confirm',
    }
    row, replayed = _begin_command(
        command='confirm_candidate',
        idempotency_key=idempotency_key,
        payload=payload,
        actor=actor,
        session=session,
        scope_fingerprint=session.scope_fingerprint,
    )
    if replayed:
        return session.decisions.get(pk=row.result['decision_id'])

    _check_revision(session, expected_revision)

    if session.state == PartVerificationState.STALE:
        raise VerificationSessionStale('The session is stale and must be reevaluated')
    if session.state != PartVerificationState.REVIEW_REQUIRED:
        raise VerificationStateConflict(
            f'Confirmation is not permitted in state {session.state}'
        )
    if not reason:
        raise VerificationContextInvalid('A confirmation rationale is required')
    if _is_past(session.expires_at):
        raise VerificationSessionExpired('The evaluation validity window has passed')

    evaluation = (
        session.candidate_evaluations
        .select_for_update()
        .filter(pk=evaluation_id, session_revision=session.revision)
        .first()
    )
    if evaluation is None:
        raise VerificationNotFound('Candidate evaluation not found')
    if not evaluation.eligible or evaluation.rejected:
        raise VerificationCandidateIneligible(
            'Only an eligible, unrejected candidate can be confirmed'
        )

    _check_policy_current(session)

    # Lock the exact candidate row before rebuilding its current facts
    candidate = (
        Part.objects.select_for_update().filter(pk=evaluation.candidate_id).first()
    )
    if candidate is None:
        raise VerificationNotFound('Candidate part not found')

    build = _require_current_snapshot(session, actor)

    # The candidate's own facts are outside the session source observation;
    # recheck its snapshot and hard results explicitly.
    if fingerprint(snapshot_candidate(candidate)) != evaluation.candidate_fingerprint:
        raise _StaleDetected(
            'candidate_changed',
            VerificationCandidateStale(
                'The candidate catalog identity changed after evaluation'
            ),
        )

    specs = policy_module.policy_requirements(session.policy)
    requirements_by_key = {item['key']: item for item in build.requirements}
    recheck = evaluate_candidate(candidate, specs, requirements_by_key)
    if not recheck.eligible:
        raise _StaleDetected(
            'candidate_changed',
            VerificationCandidateStale(
                'The candidate no longer passes every hard rule'
            ),
        )

    snapshot = _decision_snapshot(session, build, evaluation=evaluation)
    decision = _create_decision(
        session,
        actor,
        DecisionKind.CONFIRMED,
        snapshot,
        evaluation=evaluation,
        reason=reason,
    )

    session.state = PartVerificationState.CONFIRMED
    session.save()

    _append_event(
        session,
        EventType.SESSION_CONFIRMED,
        actor=actor,
        correlation_id=correlation_id,
        metadata={'decision_id': decision.pk, 'candidate': evaluation.candidate_id},
    )
    _complete_command(row, {'decision_id': decision.pk})
    return decision


def mark_no_safe_match(
    *,
    session_id,
    actor,
    expected_revision,
    idempotency_key: str,
    reason: str,
    correlation_id: str = '',
):
    """Record a complete, safe no-match decision as a first-class outcome."""
    try:
        with transaction.atomic():
            return _mark_no_safe_match_locked(
                session_id=session_id,
                actor=actor,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                reason=reason,
                correlation_id=correlation_id,
            )
    except _StaleDetected as stale:
        _apply_stale(session_id, stale.reason, actor=actor)
        raise stale.error from None


def _mark_no_safe_match_locked(
    *, session_id, actor, expected_revision, idempotency_key, reason, correlation_id
):
    """Locked no-safe-match body; see ``mark_no_safe_match``."""
    _require_enabled('AIMMS_RPF_CONFIRMATION_ENABLED')
    require_permission(actor, PERM_CONFIRM)

    session = _locked_session(session_id)
    _require_session_scope(actor, session)

    # Replay resolves before state/revision guards (see confirm_candidate)
    payload = {
        'session': session.pk,
        'expected_revision': expected_revision,
        'command': 'no_safe_match',
    }
    row, replayed = _begin_command(
        command='mark_no_safe_match',
        idempotency_key=idempotency_key,
        payload=payload,
        actor=actor,
        session=session,
        scope_fingerprint=session.scope_fingerprint,
    )
    if replayed:
        return session.decisions.get(pk=row.result['decision_id'])

    _check_revision(session, expected_revision)

    if session.state == PartVerificationState.STALE:
        raise VerificationSessionStale('The session is stale and must be reevaluated')
    if session.state != PartVerificationState.REVIEW_REQUIRED:
        raise VerificationStateConflict(
            f'No-safe-match is not permitted in state {session.state}'
        )
    if not reason:
        raise VerificationContextInvalid('A no-safe-match rationale is required')
    if _is_past(session.expires_at):
        raise VerificationSessionExpired('The evaluation validity window has passed')

    if not session.universe_complete:
        raise VerificationNoSafeMatchInvalid(
            'The candidate universe was capped; an exhaustive no-match claim '
            'is not permitted',
            blockers=[{'code': BlockerCodes.SEARCH_LIMIT_REACHED}],
        )

    eligible = session.candidate_evaluations.filter(
        session_revision=session.revision, eligible=True
    )
    if eligible.exists():
        raise VerificationNoSafeMatchInvalid(
            'Eligible candidates exist; no-safe-match is not a rejection shortcut'
        )

    _check_policy_current(session)
    build = _require_current_snapshot(session, actor)

    snapshot = _decision_snapshot(session, build)
    decision = _create_decision(
        session, actor, DecisionKind.NO_SAFE_MATCH, snapshot, reason=reason
    )

    session.state = PartVerificationState.NO_SAFE_MATCH
    session.save()

    _append_event(
        session,
        EventType.NO_SAFE_MATCH_RECORDED,
        actor=actor,
        correlation_id=correlation_id,
        metadata={'decision_id': decision.pk},
    )
    _complete_command(row, {'decision_id': decision.pk})
    return decision


@transaction.atomic
def invalidate_session(
    *, session_id, actor, idempotency_key: str, reason: str, correlation_id: str = ''
):
    """Explicitly invalidate a decided session, making it unusable downstream."""
    _require_enabled()
    require_permission(actor, PERM_INVALIDATE)

    session = _locked_session(session_id)
    _require_session_scope(actor, session)

    # Replay resolves before the state guard so an exact retry of a completed
    # invalidation returns cleanly after the session became STALE.
    payload = {'session': session.pk, 'reason': reason, 'command': 'invalidate'}
    row, replayed = _begin_command(
        command='invalidate_session',
        idempotency_key=idempotency_key,
        payload=payload,
        actor=actor,
        session=session,
        scope_fingerprint=session.scope_fingerprint,
    )
    if replayed:
        return session

    if session.state not in (
        PartVerificationState.CONFIRMED,
        PartVerificationState.NO_SAFE_MATCH,
    ):
        raise VerificationStateConflict(
            f'Invalidation is not permitted in state {session.state}'
        )
    if not reason:
        raise VerificationContextInvalid('An invalidation reason is required')

    _mark_stale(session, 'invalidated', actor=actor, metadata={'reason': reason})
    _complete_command(row, {'state': session.state})
    return session


@transaction.atomic
def cancel_session(
    *,
    session_id,
    actor,
    idempotency_key: str,
    reason: str = '',
    correlation_id: str = '',
):
    """Cancel a session explicitly; history and prior use evidence remain."""
    _require_enabled()
    require_permission(actor, PERM_CHANGE)

    session = _locked_session(session_id)
    _require_session_scope(actor, session)

    if session.state == PartVerificationState.CANCELLED:
        return session
    if session.state not in CANCELLABLE_STATES:
        raise VerificationStateConflict(
            f'Cancellation is not permitted in state {session.state}'
        )

    payload = {'session': session.pk, 'command': 'cancel'}
    row, replayed = _begin_command(
        command='cancel_session',
        idempotency_key=idempotency_key,
        payload=payload,
        actor=actor,
        session=session,
        scope_fingerprint=session.scope_fingerprint,
    )
    if replayed:
        return session

    session.state = PartVerificationState.CANCELLED
    session.save(update_fields=['state', 'updated_at'])
    _append_event(
        session,
        EventType.SESSION_CANCELLED,
        actor=actor,
        reason=reason or 'cancelled',
        correlation_id=correlation_id,
    )
    _complete_command(row, {'state': session.state})
    return session


def current_observation_preview(session) -> dict:
    """Build an unlocked preview of current facts versus the decision baseline.

    Preview carries no authority (spec section 14.1): it never marks the
    session stale and never permits use.
    """
    decision = session.current_decision
    if decision is None:
        return {'severity': None, 'differences': []}

    baseline = decision.decision_snapshot.get('observation', {})
    current = build_source_observation(session)
    result = classify(baseline, current, session.policy)
    return {'severity': result.severity, 'differences': result.differences}


def _final_observation(session, decision):
    """Rebuild the current observation and classify it against the baseline.

    The selected candidate's own facts sit outside the session source
    observation, so its snapshot fingerprint is compared explicitly.
    """
    baseline = decision.decision_snapshot.get('observation', {})
    current = build_source_observation(session)
    revalidation = classify(baseline, current, session.policy)

    if decision.selected_part_id is not None:
        selected_current = fingerprint(snapshot_candidate(decision.selected_part))
        baseline_selected = decision.decision_snapshot.get('selected', {}).get(
            'candidate_fingerprint'
        )
        if selected_current != baseline_selected:
            revalidation.severity = DifferenceSeverity.MATERIAL_REVIEW
            revalidation.differences.append({
                'path': 'selected.candidate_fingerprint',
                'baseline': baseline_selected,
                'current': selected_current,
                'severity': DifferenceSeverity.MATERIAL_REVIEW,
            })
    return current, revalidation


def validate_and_bind_use(
    *,
    decision_id,
    actor,
    consumer_kind: str,
    consumer_action: str,
    idempotency_key: str,
    consumer_model: str = '',
    consumer_object_id: str = '',
    expected_purpose: str | None = None,
    expected_work_order_id=None,
    expected_job_kit_line_id=None,
    expected_requested_part_id=None,
    expected_selected_part_id=None,
    expected_scope=None,
    command_hash: str = '',
    correlation_id: str = '',
):
    """Validate a decision for one exact consumer effect and bind one use.

    Implements the common consumer contract (spec section 13.1) and the
    current-use predicate (spec section 6.4). Only a passing final observation
    creates a use row; every failure carries a stable consumer code. A failing
    final observation persists STALE in its own transaction before raising.
    """
    try:
        with transaction.atomic():
            return _validate_and_bind_use_locked(
                decision_id=decision_id,
                actor=actor,
                consumer_kind=consumer_kind,
                consumer_action=consumer_action,
                idempotency_key=idempotency_key,
                consumer_model=consumer_model,
                consumer_object_id=consumer_object_id,
                expected_purpose=expected_purpose,
                expected_work_order_id=expected_work_order_id,
                expected_job_kit_line_id=expected_job_kit_line_id,
                expected_requested_part_id=expected_requested_part_id,
                expected_selected_part_id=expected_selected_part_id,
                expected_scope=expected_scope,
                command_hash=command_hash,
                correlation_id=correlation_id,
            )
    except _StaleDetected as stale:
        _apply_stale(
            stale.session_id, stale.reason, actor=actor, metadata=stale.metadata
        )
        raise stale.error from None


def _validate_and_bind_use_locked(
    *,
    decision_id,
    actor,
    consumer_kind,
    consumer_action,
    idempotency_key,
    consumer_model,
    consumer_object_id,
    expected_purpose,
    expected_work_order_id,
    expected_job_kit_line_id,
    expected_requested_part_id,
    expected_selected_part_id,
    expected_scope,
    command_hash,
    correlation_id,
):
    """Locked consumer-binding body; see ``validate_and_bind_use``."""
    from part.verification_models import PartVerificationDecision, PartVerificationUse

    _require_enabled()

    decision = (
        PartVerificationDecision.objects
        .select_related('session', 'policy')
        .filter(pk=decision_id)
        .first()
    )
    if decision is None:
        raise VerificationUseError(
            'Unknown verification decision',
            code=ConsumerCodes.PART_VERIFICATION_NOT_CONFIRMED,
        )

    session = _locked_session(decision.session_id)

    try:
        actor_scopes = scope_module.scope_for_actor(actor)
    except scope_module.VerificationScopeError as error:
        raise _map_scope_error(error) from error

    if _session_scope(session) not in actor_scopes:
        raise VerificationUseError(
            'Verification scope does not match the consumer scope',
            code=ConsumerCodes.PART_VERIFICATION_SCOPE_MISMATCH,
        )
    if expected_scope is not None and _session_scope(session) != expected_scope:
        raise VerificationUseError(
            'Verification scope does not match the consumer scope',
            code=ConsumerCodes.PART_VERIFICATION_SCOPE_MISMATCH,
        )

    # An exact replay of an already-bound effect returns the recorded use
    # before any state or revalidation checks: the effect already happened and
    # the use row is its immutable evidence (spec section 12.1 replay rules).
    existing = PartVerificationUse.objects.filter(
        consumer_kind=consumer_kind,
        consumer_action=consumer_action,
        idempotency_key=idempotency_key,
    ).first()
    if existing is not None:
        if (
            existing.command_hash == command_hash
            and existing.decision_id == decision.pk
        ):
            return existing
        raise VerificationUseError(
            'This consumer effect key is already bound differently',
            code=ConsumerCodes.PART_VERIFICATION_USE_CONFLICT,
        )

    if decision.kind == DecisionKind.NO_SAFE_MATCH:
        raise VerificationUseError(
            'A no-safe-match decision blocks selecting effects for its context',
            code=ConsumerCodes.PART_VERIFICATION_NO_SAFE_MATCH,
        )

    if session.state == PartVerificationState.STALE:
        raise VerificationUseError(
            'The verification is stale', code=ConsumerCodes.PART_VERIFICATION_STALE
        )
    if (
        session.state != PartVerificationState.CONFIRMED
        or session.current_decision_id != decision.pk
    ):
        raise VerificationUseError(
            'The decision is not the current confirmed decision',
            code=ConsumerCodes.PART_VERIFICATION_NOT_CONFIRMED,
        )

    if _is_past(decision.valid_until):
        raise VerificationUseError(
            'The verification decision has expired',
            code=ConsumerCodes.PART_VERIFICATION_EXPIRED,
        )

    try:
        _check_policy_current(session)
    except VerificationPolicyUnavailable as error:
        raise VerificationUseError(
            str(error), code=ConsumerCodes.PART_VERIFICATION_POLICY_INVALID
        ) from error

    if expected_purpose is not None and session.purpose != expected_purpose:
        raise VerificationUseError(
            'The verification purpose does not match the consumer command',
            code=ConsumerCodes.PART_VERIFICATION_PURPOSE_MISMATCH,
        )
    if (
        expected_work_order_id is not None
        and session.work_order_id != expected_work_order_id
    ) or (
        expected_job_kit_line_id is not None
        and session.job_kit_line_id != expected_job_kit_line_id
    ):
        raise VerificationUseError(
            'The verification context does not match the consumer command',
            code=ConsumerCodes.PART_VERIFICATION_CONTEXT_MISMATCH,
        )

    if (
        expected_requested_part_id is not None
        and session.requested_part_id != expected_requested_part_id
    ):
        raise VerificationUseError(
            'The verified requested part does not match the consumer command',
            code=ConsumerCodes.PART_VERIFICATION_REQUESTED_PART_MISMATCH,
        )
    if (
        expected_selected_part_id is not None
        and decision.selected_part_id != expected_selected_part_id
    ):
        raise VerificationUseError(
            'The verified selected part does not match the consumer command',
            code=ConsumerCodes.PART_VERIFICATION_SELECTED_PART_MISMATCH,
        )

    # Final observation under the session lock
    try:
        current, revalidation = _final_observation(session, decision)
    except Exception as error:
        raise VerificationRevalidationIndeterminate(
            'Current facts cannot be observed safely'
        ) from error

    if revalidation.severity == DifferenceSeverity.INDETERMINATE_BLOCK:
        raise VerificationUseError(
            'Current facts cannot be observed safely',
            code=ConsumerCodes.PART_VERIFICATION_REVALIDATION_INDETERMINATE,
        )

    if not revalidation.usable:
        raise _StaleDetected(
            'revalidation_difference',
            VerificationUseError(
                'The verified facts changed before the effect',
                code=ConsumerCodes.PART_VERIFICATION_STALE,
            ),
            session_id=session.pk,
            metadata={'differences': revalidation.differences[:20]},
        )

    final_observation_hash = hash_canonical(HashDomains.OBSERVATION, current)

    use = PartVerificationUse.objects.create(
        decision=decision,
        consumer_kind=consumer_kind,
        consumer_model=consumer_model,
        consumer_object_id=str(consumer_object_id or ''),
        consumer_action=consumer_action,
        scope_fingerprint=session.scope_fingerprint,
        final_observation_hash=final_observation_hash,
        command_hash=command_hash,
        idempotency_key=idempotency_key,
        actor=actor if getattr(actor, 'pk', None) else None,
    )

    _append_event(
        session,
        EventType.USE_BOUND,
        actor=actor,
        correlation_id=correlation_id,
        metadata={
            'use_id': use.pk,
            'consumer_kind': consumer_kind,
            'consumer_action': consumer_action,
        },
    )
    return use


__all__ = [
    'VerificationCommandError',
    'attach_evidence',
    'cancel_session',
    'confirm_candidate',
    'create_session',
    'current_observation_preview',
    'decide_evidence',
    'evaluate_session',
    'invalidate_session',
    'mark_no_safe_match',
    'reevaluate_session',
    'reject_candidate',
    'validate_and_bind_use',
]
