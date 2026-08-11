"""Closeout capture, extraction, and field-decision commands (Feature #15).

A capture owns the narrative source; a proposal owns untrusted extraction
output; a field decision owns the explicit human promotion of one field. None
of these are truth — the authoritative ``WorkOrderCloseout`` is only written by
the existing completion transaction, which consumes a REVIEWED capture.

Model inference never runs inside a database transaction: extraction claims
the capture, calls the configured extractor outside any transaction, then
validates and stores the proposal in a second transaction (NFR-CO-003).
"""

import hashlib
import logging
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from tasks.closeout_models import (
    ACTIVE_CAPTURE_STATUSES,
    CloseoutCapture,
    CloseoutCaptureRevision,
    CloseoutCaptureStatus,
    CloseoutFieldDecision,
    CloseoutProposal,
    CloseoutProposalStatus,
    CloseoutSourceType,
)
from tasks.models import WorkOrderLifecycle
from tasks.permissions import require_permission
from tasks.services.closeout_extraction import (
    EXTRACTION_FIELDS,
    EXTRACTION_SCHEMA_VERSION,
    ExtractionUnavailable,
    content_hash,
    resolve_extractor,
    validate_extraction_output,
)
from tasks.services.work_orders import (
    WorkOrderCommandError,
    _append_result,
    _canonical_hash,
    _locked_work_order,
    _replay_or_none,
    _require_no_packet,
    _require_scope,
    _require_version,
)

logger = logging.getLogger(__name__)

CAPTURE_CLOSEOUT = 'tasks.capture_closeout'
REVIEW_CLOSEOUT = 'tasks.review_closeout'

REQUIRED_DECISION_FIELDS = ('action', 'result', 'verification_summary')

DECIDABLE_FIELDS = (*EXTRACTION_FIELDS, 'follow_up_required')

# Lifecycle states in which a closeout narrative may be captured.
CAPTURE_ELIGIBLE_STATES = (WorkOrderLifecycle.IN_PROGRESS, WorkOrderLifecycle.VERIFYING)


class CaptureError(WorkOrderCommandError):
    """A capture command could not be applied."""

    code = 'CAPTURE_INVALID'


class CaptureStaleRevision(WorkOrderCommandError):  # noqa: N818 - established command error name
    """The caller edited against a superseded narrative revision."""

    code = 'CAPTURE_STALE_REVISION'


class DecisionRequired(WorkOrderCommandError):  # noqa: N818 - established command error name
    """A promoted field is missing its explicit human decision."""

    code = 'DECISION_REQUIRED'


class VoiceHandoffUnavailable(WorkOrderCommandError):  # noqa: N818 - established command error name
    """The Voice handoff destination is disabled in this deployment."""

    code = 'CLOSEOUT_DISABLED'


def wizard_enabled() -> bool:
    """Whether the closeout capture surface is enabled at all."""
    return bool(getattr(settings, 'AIMMS_CLOSEOUT_WIZARD_ENABLED', False))


def _require_wizard():
    if not wizard_enabled():
        raise CaptureError('The closeout wizard is disabled in this deployment')


def _max_narrative_chars() -> int:
    return int(getattr(settings, 'AIMMS_CLOSEOUT_MAX_NARRATIVE_CHARS', 20000))


def _narrative_hash(narrative: str) -> str:
    return hashlib.sha256(narrative.encode()).hexdigest()


def _min_narrative_chars() -> int:
    return int(getattr(settings, 'AIMMS_CLOSEOUT_MIN_NARRATIVE_CHARS', 80))


# Coaching warnings (S30 E5): deterministic quality nudges computed from the
# work order's own state, merged into the proposal warning set so the review
# step's existing alert surfaces them. Strings obey the extraction contract's
# 64-char bound.
_COACH_THIN_NARRATIVE = 'Narrative is thin; add cause, action and result detail'
_COACH_READINGS = 'Required readings are unresolved'
_COACH_PART_USAGE = 'Part usage rows are unresolved'

_MAX_PROPOSAL_WARNINGS = 32


def coaching_warnings(work_order, narrative: str) -> list[str]:
    """Return deterministic closeout-quality warnings; never raises."""
    from tasks.services.closeout_reconcile import (
        unresolved_required_readings,
        unresolved_usage_rows,
    )

    warnings: list[str] = []
    try:
        if len((narrative or '').strip()) < _min_narrative_chars():
            warnings.append(_COACH_THIN_NARRATIVE)
        if unresolved_required_readings(work_order):
            warnings.append(_COACH_READINGS)
        variances, candidates = unresolved_usage_rows(work_order)
        if variances or candidates:
            warnings.append(_COACH_PART_USAGE)
    except Exception:  # pragma: no cover - coaching must never block closeout
        logger.warning('closeout coaching evaluation failed', exc_info=False)
    return warnings


def _merge_warnings(base: list[str], extra: list[str]) -> list[str]:
    """Append new warnings without duplicates, keeping the contract cap."""
    merged = list(base)
    for warning in extra:
        if warning not in merged:
            merged.append(warning)
    return merged[:_MAX_PROPOSAL_WARNINGS]


def _validate_narrative(narrative: str):
    if not (narrative or '').strip():
        raise CaptureError('A closeout narrative is required')
    if len(narrative) > _max_narrative_chars():
        raise CaptureError('The closeout narrative exceeds the configured bound')


def _require_capture_eligible(work_order):
    if work_order.lifecycle_status not in CAPTURE_ELIGIBLE_STATES:
        raise CaptureError('Closeout capture requires in-progress or verifying work')


def _locked_capture(work_order, capture_id) -> CloseoutCapture:
    # Lock only the capture row (of=('self',)); current_revision is a nullable
    # FK and PostgreSQL rejects FOR UPDATE across its outer join.
    capture = (
        CloseoutCapture.objects
        .select_for_update(of=('self',))
        .select_related('current_revision')
        .filter(pk=capture_id, work_order=work_order)
        .first()
    )
    if capture is None:
        raise CaptureError('Capture does not belong to this work order')
    return capture


def active_capture(work_order) -> CloseoutCapture | None:
    """Return the single in-flight capture for a work order, if any."""
    return (
        CloseoutCapture.objects
        .filter(work_order=work_order, status__in=ACTIVE_CAPTURE_STATUSES)
        .select_related('current_revision')
        .order_by('-pk')
        .first()
    )


@transaction.atomic
def create_capture(
    *,
    work_order_id,
    actor,
    narrative,
    expected_version,
    idempotency_key,
    correlation_id=None,
):
    """Create a typed narrative capture against an active work order."""
    _require_wizard()
    work_order = _locked_work_order(work_order_id)
    narrative = narrative or ''
    payload = {
        'work_order_id': work_order_id,
        'expected_version': expected_version,
        'narrative_hash': _narrative_hash(narrative),
    }
    request_hash = _canonical_hash('closeout_capture', actor, payload)
    replay = _replay_or_none(work_order, idempotency_key, request_hash)
    if replay:
        return replay

    _require_version(work_order, expected_version)
    require_permission(actor, CAPTURE_CLOSEOUT)
    _require_scope(actor, work_order)
    _require_no_packet(work_order)
    _require_capture_eligible(work_order)
    _validate_narrative(narrative)
    if active_capture(work_order) is not None:
        raise CaptureError('An active closeout capture already exists')

    capture = CloseoutCapture.objects.create(
        work_order=work_order, source_type=CloseoutSourceType.TYPED, created_by=actor
    )
    revision = CloseoutCaptureRevision.objects.create(
        capture=capture,
        revision=1,
        narrative=narrative,
        source_content_hash=_narrative_hash(narrative),
        work_order_version=work_order.lifecycle_version,
        created_by=actor,
    )
    capture.current_revision = revision
    capture.save(update_fields=['current_revision'])
    return _append_result(
        work_order=work_order,
        actor=actor,
        command='closeout_capture',
        event_type='CLOSEOUT_CAPTURE_CREATED',
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        reason='',
        correlation_id=correlation_id or uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata={'capture_id': capture.pk, 'revision': revision.revision},
    )


@transaction.atomic
def revise_capture(
    *,
    work_order_id,
    capture_id,
    actor,
    narrative,
    expected_revision,
    expected_version,
    idempotency_key,
    correlation_id=None,
):
    """Append a new immutable narrative revision; prior proposals stay bound."""
    _require_wizard()
    work_order = _locked_work_order(work_order_id)
    narrative = narrative or ''
    payload = {
        'work_order_id': work_order_id,
        'capture_id': capture_id,
        'expected_revision': expected_revision,
        'expected_version': expected_version,
        'narrative_hash': _narrative_hash(narrative),
    }
    request_hash = _canonical_hash('closeout_capture_revise', actor, payload)
    replay = _replay_or_none(work_order, idempotency_key, request_hash)
    if replay:
        return replay

    _require_version(work_order, expected_version)
    require_permission(actor, CAPTURE_CLOSEOUT)
    _require_scope(actor, work_order)
    capture = _locked_capture(work_order, capture_id)
    if capture.status not in ACTIVE_CAPTURE_STATUSES:
        raise CaptureError(f'A {capture.status} capture cannot be revised')
    if capture.source_type == CloseoutSourceType.VOICE:
        raise CaptureError(
            'A voice capture snapshot is immutable; correct it in Voice review '
            'and hand off a new accepted revision'
        )
    current = capture.current_revision
    if current is None or current.revision != expected_revision:
        raise CaptureStaleRevision('The narrative was revised by someone else')
    _validate_narrative(narrative)

    revision = CloseoutCaptureRevision.objects.create(
        capture=capture,
        revision=current.revision + 1,
        narrative=narrative,
        source_content_hash=_narrative_hash(narrative),
        work_order_version=work_order.lifecycle_version,
        supersedes=current,
        created_by=actor,
    )
    capture.current_revision = revision
    capture.status = CloseoutCaptureStatus.OPEN
    capture.save(update_fields=['current_revision', 'status'])
    return _append_result(
        work_order=work_order,
        actor=actor,
        command='closeout_capture_revise',
        event_type='CLOSEOUT_CAPTURE_REVISED',
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        reason='',
        correlation_id=correlation_id or uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata={'capture_id': capture.pk, 'revision': revision.revision},
    )


@transaction.atomic
def abandon_capture(
    *,
    work_order_id,
    capture_id,
    actor,
    expected_version,
    idempotency_key,
    reason='',
    correlation_id=None,
):
    """Explicitly abandon an in-flight capture; rows remain auditable."""
    _require_wizard()
    work_order = _locked_work_order(work_order_id)
    payload = {
        'work_order_id': work_order_id,
        'capture_id': capture_id,
        'expected_version': expected_version,
        'reason': reason,
    }
    request_hash = _canonical_hash('closeout_capture_abandon', actor, payload)
    replay = _replay_or_none(work_order, idempotency_key, request_hash)
    if replay:
        return replay

    _require_version(work_order, expected_version)
    require_permission(actor, CAPTURE_CLOSEOUT)
    _require_scope(actor, work_order)
    capture = _locked_capture(work_order, capture_id)
    if capture.status == CloseoutCaptureStatus.CONSUMED:
        raise CaptureError('A consumed capture cannot be abandoned')
    capture.status = CloseoutCaptureStatus.ABANDONED
    capture.save(update_fields=['status'])
    return _append_result(
        work_order=work_order,
        actor=actor,
        command='closeout_capture_abandon',
        event_type='CLOSEOUT_CAPTURE_ABANDONED',
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        reason=reason,
        correlation_id=correlation_id or uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata={'capture_id': capture.pk},
    )


def _work_order_shape(work_order) -> dict:
    """Display-string shape for extraction; deliberately id-free."""
    from tasks.models import WorkOrderProcedureApplication

    application = (
        WorkOrderProcedureApplication.objects
        .filter(work_order=work_order, primary=True)
        .order_by('pk')
        .first()
    )
    step_labels = []
    if application is not None:
        step_labels = [
            str(execution.step_snapshot.get('title', ''))
            for execution in application.step_executions.order_by('sequence')
        ]
    return {
        'work_order_type': str(work_order.work_order_type or ''),
        'machine_name': str(work_order.machine.name) if work_order.machine_id else '',
        'step_labels': step_labels,
    }


def _extraction_model_provenance(raw_output) -> dict[str, str]:
    """Copy trusted binding metadata without admitting it into the JSON schema.

    The deployment adapter attaches provenance as an out-of-band attribute on
    its dict subclass. Model-produced JSON cannot set that attribute. Other
    configured extractors retain the explicit Django label as a compatibility
    fallback, but blank labels are not recorded as if they identified a model.
    """
    supplied = getattr(raw_output, 'model_provenance', None)
    if isinstance(supplied, dict):
        provenance = {
            key: value.strip()
            for key in ('deployment', 'model', 'run_id')
            if isinstance((value := supplied.get(key)), str)
            and value.strip()
            and len(value.strip()) <= 255
        }
        if provenance:
            return provenance

    configured = str(
        getattr(settings, 'AIMMS_CLOSEOUT_EXTRACTION_MODEL', '') or ''
    ).strip()
    return {'model': configured} if configured else {}


def _live_proposal(revision) -> CloseoutProposal | None:
    return (
        CloseoutProposal.objects
        .filter(capture_revision=revision)
        .exclude(status=CloseoutProposalStatus.SUPERSEDED)
        .first()
    )


def _revert_capture_to_open(capture_id):
    with transaction.atomic():
        capture = CloseoutCapture.objects.select_for_update().get(pk=capture_id)
        if capture.status == CloseoutCaptureStatus.EXTRACTING:
            capture.status = CloseoutCaptureStatus.OPEN
            capture.save(update_fields=['status'])


def request_extraction(*, work_order_id, capture_id, actor):
    """Run schema-only extraction for the capture's current revision.

    Idempotent per revision: an existing live proposal is returned unchanged.
    The extractor call happens outside any database transaction; a failed or
    invalid run leaves the capture ``OPEN`` with a visible retry path.
    """
    _require_wizard()
    with transaction.atomic():
        work_order = _locked_work_order(work_order_id)
        require_permission(actor, CAPTURE_CLOSEOUT)
        _require_scope(actor, work_order)
        capture = _locked_capture(work_order, capture_id)
        revision = capture.current_revision
        if revision is None:
            raise CaptureError('Capture has no narrative revision')
        existing = _live_proposal(revision)
        if existing is not None:
            if capture.status in {
                CloseoutCaptureStatus.OPEN,
                CloseoutCaptureStatus.EXTRACTING,
            }:
                capture.status = CloseoutCaptureStatus.PROPOSED
                capture.save(update_fields=['status'])
            return existing
        if capture.status == CloseoutCaptureStatus.EXTRACTING:
            raise CaptureError('Extraction is already in progress')
        if capture.status != CloseoutCaptureStatus.OPEN:
            raise CaptureError(f'A {capture.status} capture cannot be extracted')
        capture.status = CloseoutCaptureStatus.EXTRACTING
        capture.save(update_fields=['status'])
        narrative = revision.narrative
        shape = _work_order_shape(work_order)

    try:
        extractor = resolve_extractor()
        raw_output = extractor(narrative, shape)
        model_provenance = _extraction_model_provenance(raw_output)
    except WorkOrderCommandError:
        _revert_capture_to_open(capture_id)
        raise
    except Exception as exc:
        _revert_capture_to_open(capture_id)
        raise ExtractionUnavailable('The closeout extractor call failed') from exc

    try:
        document = validate_extraction_output(raw_output, narrative)
    except WorkOrderCommandError:
        _revert_capture_to_open(capture_id)
        raise
    document['warnings'] = _merge_warnings(
        document['warnings'], coaching_warnings(work_order, narrative)
    )

    with transaction.atomic():
        capture = CloseoutCapture.objects.select_for_update().get(pk=capture_id)
        existing = _live_proposal(revision)
        if existing is not None:
            return existing
        if (
            capture.current_revision_id != revision.pk
            or capture.status != CloseoutCaptureStatus.EXTRACTING
        ):
            raise CaptureStaleRevision(
                'The capture changed while extraction was running'
            )
        proposal = CloseoutProposal.objects.create(
            capture_revision=revision,
            schema_version=EXTRACTION_SCHEMA_VERSION,
            extractor=getattr(extractor, '__name__', 'configured'),
            model_provenance=model_provenance,
            fields=document['fields'],
            part_candidates=document['part_candidates'],
            reading_candidates=document['reading_candidates'],
            warnings=document['warnings'],
            content_hash=content_hash(document),
        )
        capture.status = CloseoutCaptureStatus.PROPOSED
        capture.save(update_fields=['status'])
        return proposal


def _manual_proposal(revision, work_order) -> CloseoutProposal:
    warnings = _merge_warnings([], coaching_warnings(work_order, revision.narrative))
    document = {
        'schema_version': EXTRACTION_SCHEMA_VERSION,
        'fields': {},
        'part_candidates': [],
        'reading_candidates': [],
        'warnings': warnings,
    }
    return CloseoutProposal.objects.create(
        capture_revision=revision,
        schema_version=EXTRACTION_SCHEMA_VERSION,
        extractor='manual',
        fields={},
        warnings=warnings,
        content_hash=content_hash(document),
    )


def _normalize_decision(entry, proposal) -> dict:
    field_path = str(entry.get('field_path', ''))
    if field_path not in DECIDABLE_FIELDS:
        raise DecisionRequired(f'Unknown closeout field: {field_path!r}')
    decision = str(entry.get('decision', ''))
    if decision not in {'accepted', 'edited', 'rejected'}:
        raise DecisionRequired(f'Unknown decision {decision!r} for {field_path!r}')

    extracted = (proposal.fields or {}).get(field_path)
    origin = 'extracted' if extracted is not None else 'manual'
    if decision == 'accepted':
        if extracted is None:
            raise DecisionRequired(f'{field_path!r} has no extracted value to accept')
        final_value = extracted.get('value')
    elif decision == 'edited':
        if 'final_value' not in entry:
            raise DecisionRequired(f'{field_path!r} requires an edited final value')
        final_value = entry['final_value']
    else:
        final_value = None
    return {
        'field_path': field_path,
        'origin': origin,
        'decision': decision,
        'final_value': final_value,
    }


def _decision_value_present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def decisions_cover_required_fields(proposal) -> list[str]:
    """Return required closeout fields still missing an affirmative decision."""
    decided = {
        row.field_path: row
        for row in proposal.decisions.all()
        if row.decision in {'accepted', 'edited'}
    }
    return [
        name
        for name in REQUIRED_DECISION_FIELDS
        if name not in decided or not _decision_value_present(decided[name].final_value)
    ]


@transaction.atomic
def record_decisions(
    *,
    work_order_id,
    capture_id,
    actor,
    decisions,
    expected_version,
    idempotency_key,
    correlation_id=None,
):
    """Record a batch of explicit per-field human promotion decisions."""
    _require_wizard()
    work_order = _locked_work_order(work_order_id)
    payload = {
        'work_order_id': work_order_id,
        'capture_id': capture_id,
        'expected_version': expected_version,
        'decisions': decisions,
    }
    request_hash = _canonical_hash('closeout_decisions', actor, payload)
    replay = _replay_or_none(work_order, idempotency_key, request_hash)
    if replay:
        return replay

    _require_version(work_order, expected_version)
    require_permission(actor, REVIEW_CLOSEOUT)
    _require_scope(actor, work_order)
    capture = _locked_capture(work_order, capture_id)
    if capture.status not in {
        CloseoutCaptureStatus.OPEN,
        CloseoutCaptureStatus.PROPOSED,
        CloseoutCaptureStatus.REVIEWED,
    }:
        raise CaptureError(f'A {capture.status} capture cannot record decisions')
    revision = capture.current_revision
    if revision is None:
        raise CaptureError('Capture has no narrative revision')
    if not isinstance(decisions, list) or not decisions:
        raise DecisionRequired('At least one field decision is required')

    proposal = _live_proposal(revision)
    if proposal is None:
        proposal = _manual_proposal(revision, work_order)

    now = timezone.now()
    for entry in decisions:
        normalized = _normalize_decision(entry, proposal)
        CloseoutFieldDecision.objects.update_or_create(
            proposal=proposal,
            field_path=normalized['field_path'],
            defaults={
                'origin': normalized['origin'],
                'decision': normalized['decision'],
                'final_value': normalized['final_value'],
                'decided_by': actor,
                'decided_at': now,
            },
        )

    missing = decisions_cover_required_fields(proposal)
    capture.status = (
        CloseoutCaptureStatus.REVIEWED
        if not missing
        else CloseoutCaptureStatus.PROPOSED
    )
    capture.save(update_fields=['status'])
    if proposal.status == CloseoutProposalStatus.PROPOSED and not missing:
        proposal.status = CloseoutProposalStatus.REVIEWED
        proposal.save(update_fields=['status'])

    return _append_result(
        work_order=work_order,
        actor=actor,
        command='closeout_decisions',
        event_type='CLOSEOUT_DECISIONS_RECORDED',
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        reason='',
        correlation_id=correlation_id or uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata={
            'capture_id': capture.pk,
            'proposal_id': proposal.pk,
            'capture_status': capture.status,
            'missing_required_fields': missing,
        },
    )


@transaction.atomic
def accept_voice_handoff(*, voice_capture):
    """Snapshot one accepted Voice transcript revision into a closeout capture.

    Dereferences, authorizes, and copies the exact accepted revision once
    (FR-CO-001). Replaying the same ``(work_order, transcript_reference)``
    returns the existing capture; later Voice revisions never mutate the
    snapshot. The Voice service converts these errors at its own boundary.
    """
    if not wizard_enabled():
        raise VoiceHandoffUnavailable(
            'Feature #15 closeout capture is disabled in this deployment'
        )
    revision = voice_capture.accepted_revision
    if revision is None:
        raise CaptureError('Voice capture has no accepted transcript revision')
    acceptance = getattr(revision, 'acceptance', None)
    if acceptance is None or acceptance.content_hash != revision.content_hash:
        raise CaptureError('Voice transcript acceptance is missing or hash-stale')

    actor = voice_capture.owner
    work_order = _locked_work_order(voice_capture.target_work_order_id)
    require_permission(actor, CAPTURE_CLOSEOUT)
    _require_scope(actor, work_order)
    _require_no_packet(work_order)

    transcript_reference = str(revision.pk)
    existing = CloseoutCapture.objects.filter(
        work_order=work_order, transcript_reference=transcript_reference
    ).first()
    if existing is not None:
        return existing

    _require_capture_eligible(work_order)
    _validate_narrative(revision.full_text)
    if active_capture(work_order) is not None:
        raise CaptureError('An active closeout capture already exists')

    capture = CloseoutCapture.objects.create(
        work_order=work_order,
        source_type=CloseoutSourceType.VOICE,
        transcript_reference=transcript_reference,
        created_by=actor,
    )
    capture_revision = CloseoutCaptureRevision.objects.create(
        capture=capture,
        revision=1,
        narrative=revision.full_text,
        source_content_hash=revision.content_hash,
        work_order_version=work_order.lifecycle_version,
        created_by=actor,
    )
    capture.current_revision = capture_revision
    capture.save(update_fields=['current_revision'])
    _append_result(
        work_order=work_order,
        actor=actor,
        command='closeout_capture',
        event_type='CLOSEOUT_CAPTURE_CREATED',
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        reason='voice handoff',
        correlation_id=uuid.uuid4(),
        idempotency_key=f'closeout-voice:{transcript_reference}',
        request_hash=_canonical_hash(
            'closeout_capture',
            actor,
            {
                'work_order_id': work_order.pk,
                'transcript_reference': transcript_reference,
            },
        ),
        result_metadata={'capture_id': capture.pk, 'revision': 1},
    )
    return capture
