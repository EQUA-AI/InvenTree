"""Investigation findings and approved repair scope.

Two things a repair page has to be able to answer, and could not before:

* **What was actually observed?** Findings are typed rows, not sentences buried
  in a description. A reader can tell a SCADA reading from a technician's
  measurement, see its unit, and see whether anyone has checked it.
* **What was actually approved?** Approving a repair freezes a version of the
  scope. A later AI regeneration produces new preliminary content; it cannot
  rewrite what an approver signed off, because that version is a separate,
  immutable row.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import ApprovedRepairScope, RepairInvestigationFinding, RepairPacketEvent

#: A packet is a work package, not a data warehouse. Bounding the finding set
#: keeps one import from turning a repair page into a scroll of noise.
MAX_FINDINGS_PER_PACKET = 200

#: Likewise for the ordered scope a crew is expected to work through.
MAX_SCOPE_LINES = 100


class InvestigationError(Exception):
    """The finding or scope request is invalid."""

    code = 'INVESTIGATION_INVALID'


def _normalized_scope_lines(raw) -> list[dict]:
    """Validate and order the approved scope lines."""
    if not isinstance(raw, list):
        raise InvestigationError('scope_lines must be a list.')
    if len(raw) > MAX_SCOPE_LINES:
        raise InvestigationError(
            f'An approved scope may contain at most {MAX_SCOPE_LINES} lines.'
        )

    lines = []
    for index, entry in enumerate(raw):
        if isinstance(entry, str):
            action = entry.strip()
        elif isinstance(entry, dict):
            action = str(entry.get('action') or '').strip()
        else:
            raise InvestigationError(f'Scope line {index} must be text or an object.')

        if not action:
            raise InvestigationError(f'Scope line {index} is empty.')

        lines.append({'sequence': index + 1, 'action': action[:500]})

    if not lines:
        raise InvestigationError('An approved scope needs at least one line.')

    return lines


@transaction.atomic
def record_finding(
    packet,
    *,
    finding_key: str,
    observation: str,
    category: str = RepairInvestigationFinding.Category.OTHER,
    value=None,
    unit: str = '',
    evidence_source: str = '',
    snapshot=None,
    observed_at=None,
    verification: str = RepairInvestigationFinding.Verification.UNVERIFIED,
    actor=None,
    sequence: int | None = None,
) -> tuple[RepairInvestigationFinding, bool]:
    """Create or update one finding, keyed stably within the packet.

    Idempotent on ``finding_key``: a loader or importer that runs twice updates
    the row it wrote rather than adding a second copy.

    A snapshot from another machine is refused. Telemetry from a different asset
    can never be evidence for this fault, and attaching it would make the
    citation actively misleading.
    """
    if not finding_key:
        raise InvestigationError('A finding needs a stable finding_key.')
    if not observation or not observation.strip():
        raise InvestigationError('A finding needs an observation.')
    if category not in RepairInvestigationFinding.Category.values:
        raise InvestigationError(f'Unknown finding category {category!r}.')
    if verification not in RepairInvestigationFinding.Verification.values:
        raise InvestigationError(f'Unknown verification state {verification!r}.')

    if snapshot is not None and snapshot.machine_id != packet.machine_id:
        raise InvestigationError(
            'That evidence snapshot belongs to a different machine.'
        )

    existing_count = packet.findings.count()
    if sequence is None:
        sequence = existing_count + 1

    defaults = {
        'sequence': sequence,
        'category': category,
        'observation': observation.strip(),
        'value': value,
        'unit': unit[:32],
        'evidence_source': evidence_source[:200],
        'snapshot': snapshot,
        'observed_at': observed_at,
        'verification': verification,
        'recorded_by': actor if getattr(actor, 'pk', None) else None,
    }

    finding = packet.findings.filter(finding_key=finding_key).first()
    if finding is None:
        if existing_count >= MAX_FINDINGS_PER_PACKET:
            raise InvestigationError(
                f'A packet may carry at most {MAX_FINDINGS_PER_PACKET} findings.'
            )
        return (
            RepairInvestigationFinding.objects.create(
                packet=packet, finding_key=finding_key, **defaults
            ),
            True,
        )

    for field, field_value in defaults.items():
        setattr(finding, field, field_value)
    finding.save(update_fields=[*defaults, 'updated_at'])
    return finding, False


@transaction.atomic
def approve_repair_scope(
    packet,
    *,
    scope_lines,
    verified_cause: str = '',
    failure_codes=None,
    crew_size=None,
    planned_elapsed_minutes=None,
    actor=None,
    note: str = '',
) -> ApprovedRepairScope:
    """Freeze the repair scope that was approved, as a new version.

    The previous version is marked superseded rather than edited or removed, so
    what an approver agreed to stays reconstructable after the plan changes.
    """
    lines = _normalized_scope_lines(scope_lines)

    if failure_codes is not None and not isinstance(failure_codes, list):
        raise InvestigationError('failure_codes must be a list.')

    current = packet.approved_scopes.filter(superseded_at__isnull=True).first()
    now = timezone.now()

    if current is not None:
        current.superseded_at = now
        current.save(update_fields=['superseded_at'])

    next_version = (
        packet.approved_scopes
        .order_by('-version')
        .values_list('version', flat=True)
        .first()
        or 0
    ) + 1

    scope = ApprovedRepairScope.objects.create(
        packet=packet,
        version=next_version,
        verified_cause=verified_cause.strip(),
        scope_lines=lines,
        failure_codes=[str(code)[:64] for code in (failure_codes or [])],
        crew_size=crew_size,
        planned_elapsed_minutes=planned_elapsed_minutes,
        approved_by=actor if getattr(actor, 'pk', None) else None,
        approved_at=now,
        approval_note=note[:2000],
    )

    RepairPacketEvent.objects.create(
        packet=packet,
        event_type=RepairPacketEvent.EventType.ADVANCED,
        to_status=packet.status,
        actor=actor if getattr(actor, 'pk', None) else None,
        reason='Repair scope approved',
        metadata={
            'approved_scope_version': scope.version,
            'scope_line_count': len(lines),
            'superseded_version': current.version if current else None,
        },
    )

    return scope


def current_scope(packet) -> ApprovedRepairScope | None:
    """Return the approved scope currently in force, if any."""
    return packet.approved_scopes.filter(superseded_at__isnull=True).first()
