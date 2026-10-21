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

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    ApprovedRepairScope,
    RepairFindingRevision,
    RepairInvestigationFinding,
    RepairPacket,
    RepairPacketEvent,
)

#: A packet is a work package, not a data warehouse. Bounding the finding set
#: keeps one import from turning a repair page into a scroll of noise.
MAX_FINDINGS_PER_PACKET = 200

#: Likewise for the ordered scope a crew is expected to work through.
MAX_SCOPE_LINES = 100
MAX_DEPENDENCIES = 50
MAX_EVIDENCE_IDS = 50
MAX_BINDING_ID = 64


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

        line = {'sequence': index + 1, 'action': action[:500]}
        if isinstance(entry, dict):
            component = entry.get('affected_component_ref')
            revision = entry.get('procedure_revision_id')
            if component is not None:
                if (
                    not isinstance(component, str)
                    or not component.strip()
                    or len(component) > 64
                ):
                    raise InvestigationError(
                        'A component binding needs an exact nonempty reference.'
                    )
                line['affected_component_ref'] = component.strip()
            if revision is not None:
                if type(revision) is not int or revision < 1:
                    raise InvestigationError(
                        'A procedure revision binding needs a positive integer.'
                    )
                line['procedure_revision_id'] = revision
            if 'dependencies' in entry:
                dependencies = entry['dependencies']
                if not isinstance(dependencies, list):
                    raise InvestigationError('dependencies must be a list.')
                if len(dependencies) > MAX_DEPENDENCIES:
                    raise InvestigationError('Too many scope dependencies.')
                normalized = []
                seen = set()
                for raw_binding in dependencies:
                    binding = (
                        dict(raw_binding)
                        if isinstance(raw_binding, dict)
                        else raw_binding
                    )
                    if (
                        not isinstance(binding, dict)
                        or set(binding)
                        - {
                            'kind',
                            'id',
                            'affected_component_ref',
                            'procedure_revision_id',
                            'evidence_ids',
                        }
                        or binding.get('kind')
                        not in (
                            'tasks',
                            'materials',
                            'resources',
                            'permissions',
                            'criteria',
                        )
                        or not isinstance(binding.get('id'), str)
                        or not binding['id'].strip()
                        or len(binding['id']) > MAX_BINDING_ID
                    ):
                        raise InvestigationError(
                            'Each dependency requires a typed stable identity.'
                        )
                    if 'affected_component_ref' in binding and (
                        not isinstance(binding['affected_component_ref'], str)
                        or not binding['affected_component_ref'].strip()
                        or len(binding['affected_component_ref']) > 64
                    ):
                        raise InvestigationError(
                            'Dependency component reference must be nonempty text.'
                        )
                    if 'procedure_revision_id' in binding and (
                        type(binding['procedure_revision_id']) is not int
                        or binding['procedure_revision_id'] < 1
                    ):
                        raise InvestigationError(
                            'Dependency procedure revision must be a positive integer.'
                        )
                    if 'evidence_ids' in binding and (
                        not isinstance(binding['evidence_ids'], list)
                        or len(binding['evidence_ids']) > MAX_EVIDENCE_IDS
                        or any(
                            type(pk) is not int or pk < 1
                            for pk in binding['evidence_ids']
                        )
                    ):
                        raise InvestigationError(
                            'Evidence identities must be positive integers.'
                        )
                    binding['id'] = binding['id'].strip()
                    if (binding['kind'], binding['id']) in seen:
                        raise InvestigationError(
                            'Duplicate dependency identity on one line.'
                        )
                    seen.add((binding['kind'], binding['id']))
                    if 'affected_component_ref' in binding:
                        binding['affected_component_ref'] = binding[
                            'affected_component_ref'
                        ].strip()
                    normalized.append(binding)
                line['dependencies'] = normalized
        lines.append(line)

    if not lines:
        raise InvestigationError('An approved scope needs at least one line.')

    return lines


def _validate_scope_bindings(packet, lines):
    """Resolve authored identities to this machine, applied procedures and Work artifacts.

    evidence_ids names immutable InvestigationArtifact rows. Repair finding ids
    have a different namespace and cannot be inferred from an integer alone.
    """
    from tasks.investigation_models import InvestigationArtifact
    from tasks.models import WorkOrderProcedureApplication

    from assets.registry_models import AssetComponent

    components = set(
        AssetComponent.objects.filter(machine_id=packet.machine_id).values_list(
            'code', flat=True
        )
    )
    revisions = (
        set(
            WorkOrderProcedureApplication.objects.filter(
                work_order_id=packet.work_order_id
            ).values_list('revision_id', flat=True)
        )
        if packet.work_order_id
        else set()
    )
    evidence = (
        set(
            InvestigationArtifact.objects.filter(
                request__work_order_id=packet.work_order_id
            ).values_list('pk', flat=True)
        )
        if packet.work_order_id
        else set()
    )
    for line in lines:
        for row in [line, *line.get('dependencies', [])]:
            if (
                row.get('affected_component_ref') is not None
                and row['affected_component_ref'] not in components
            ):
                raise InvestigationError(
                    'Component binding must resolve on this packet machine.'
                )
            if (
                row.get('procedure_revision_id') is not None
                and row['procedure_revision_id'] not in revisions
            ):
                raise InvestigationError(
                    'Procedure revision must be applied to this Work.'
                )
            if any(pk not in evidence for pk in row.get('evidence_ids', [])):
                raise InvestigationError(
                    'Evidence must be an immutable investigation artifact of this Work.'
                )


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

    # Serialize upserts/corrections through the native parent on PostgreSQL.
    packet = RepairPacket.objects.select_for_update().get(pk=packet.pk)
    try:
        observed_at = RepairInvestigationFinding._meta.get_field(
            'observed_at'
        ).to_python(observed_at)
    except ValidationError as exc:
        raise InvestigationError('Invalid observation time.') from exc
    if observed_at is not None:
        zone = timezone.get_default_timezone()
        if settings.USE_TZ and timezone.is_naive(observed_at):
            observed_at = timezone.make_aware(observed_at, zone)
        elif not settings.USE_TZ and timezone.is_aware(observed_at):
            observed_at = timezone.make_naive(observed_at, zone)
    existing = packet.findings.filter(finding_key=finding_key).first()
    existing_count = packet.findings.count()
    if sequence is None:
        sequence = existing.sequence if existing else existing_count + 1

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

    # Preserve the observation before replacing any fact. Exact repeats remain
    # idempotent, including original attribution and timestamps.
    changed = any(getattr(finding, field) != value for field, value in defaults.items())
    if not changed:
        return finding, False
    RepairFindingRevision.objects.create(
        finding=finding,
        snapshot=finding_snapshot(finding),
        replaced_by=actor if getattr(actor, 'pk', None) else None,
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
    _validate_scope_bindings(packet, lines)

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


def finding_snapshot(finding):
    """Retain every original typed fact and its recorded attribution."""
    from tasks.work_execution.common import person

    return {
        'id': finding.pk,
        'finding_key': finding.finding_key,
        'sequence': finding.sequence,
        'category': finding.category,
        'observation': finding.observation,
        'value': finding.value,
        'unit': finding.unit,
        'evidence_source': finding.evidence_source,
        'snapshot_id': str(finding.snapshot_id) if finding.snapshot_id else None,
        'observed_at': finding.observed_at.isoformat() if finding.observed_at else None,
        'verification': finding.verification,
        'recorded_by': person(finding.recorded_by),
        'recorded_by_id': finding.recorded_by_id,
        'created_at': finding.created_at.isoformat(),
        'updated_at': finding.updated_at.isoformat(),
    }
