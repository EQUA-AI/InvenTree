"""Read-only authorized context and typed ownership for work-package intake."""

from django.contrib.auth import get_user_model
from django.core import signing
from django.utils import timezone

from tasks.permissions import ASSIGN_WORKORDER, EXECUTE_WORKORDER, require_permission
from tasks.scope import scoped_machines

from .work_packages import (
    InvalidAnomalySource,
    WorkPackageError,
    find_duplicate_repairs,
)

DRAFT_SALT = 'repair.work-intake.v1'


def assignment_candidate(actor, machine, user_id):
    """Resolve a currently active owner inside the machine's maintenance scope."""
    require_permission(actor, ASSIGN_WORKORDER)
    candidate = get_user_model().objects.filter(pk=user_id, is_active=True).first()
    if (
        candidate is None
        or not candidate.has_perm(EXECUTE_WORKORDER)
        or not scoped_machines(candidate).filter(pk=machine.pk).exists()
    ):
        raise WorkPackageError('Select active staff authorized for this machine.')
    return candidate


def assignable_staff(actor, machine, search=''):
    """Project a bounded directory without email, role or credential disclosure."""
    require_permission(actor, ASSIGN_WORKORDER)
    from django.db.models import Q

    candidates = get_user_model().objects.filter(is_active=True)
    if search:
        candidates = candidates.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(username__icontains=search)
        )
    results = []
    for candidate in candidates.order_by('first_name', 'last_name', 'username')[:200]:
        if (
            candidate.has_perm(EXECUTE_WORKORDER)
            and scoped_machines(candidate).filter(pk=machine.pk).exists()
        ):
            results.append({
                'id': candidate.pk,
                'name': candidate.get_full_name().strip() or candidate.get_username(),
                'username': candidate.get_username(),
            })
        if len(results) == 50:
            break
    return results


def intake_context(actor, machine, anomaly_id):
    """Assemble bounded same-machine facts, keeping test and measured values apart."""
    from assets.health_models import MachineAnomaly
    from common.models import Attachment
    from machine_health.services.test_alarms import targets

    from .intake_binding import provider_status
    from .intake_scenarios import prepared_scenario

    anomaly = MachineAnomaly.objects.filter(pk=anomaly_id, machine=machine).first()
    if anomaly is None:
        raise InvalidAnomalySource('The source anomaly is not on this machine.')
    rows = targets(machine)
    target = next(
        (row for row in rows if row['binding_id'] == anomaly.metrics.get('binding_id')),
        {},
    )
    owned = anomaly.work_order_id is not None or anomaly.repair_packet_id is not None
    context = {
        'machine': {'id': machine.pk, 'name': machine.name},
        'source': {
            'anomaly_id': anomaly.pk,
            'title': anomaly.title,
            'severity': anomaly.severity,
            'status': anomaly.status,
            'observed_at': anomaly.first_observed_at.isoformat(),
            'synthetic': anomaly.metrics.get('synthetic') is True,
            'test_value': anomaly.metrics.get('test_value'),
            'unit': anomaly.metrics.get('unit', ''),
            'parameter': target.get('name'),
            'component': target.get('component_name'),
            'measured_value': target.get('value')
            if target.get('quality') == 'good' and not target.get('stale')
            else None,
            'measurement_stale': target.get('stale'),
            'measurement_quality': target.get('quality'),
            'measurement_observed_at': target.get('observed_at'),
            'work_order_id': anomaly.work_order_id,
        },
        'reference_channels': [
            {
                'name': row['name'],
                'measured_value': row.get('value'),
                'unit': row['unit'],
                'stale': row.get('stale'),
                'quality': row.get('quality'),
                'observed_at': row.get('observed_at'),
                'limits': row.get('limits'),
            }
            for row in rows[:100]
        ],
        'document_metadata_only': list(
            Attachment.objects.filter(
                model_type='assetmachine', model_id=machine.pk
            ).values('pk', 'comment')[:20]
        ),
    }
    duplicates = find_duplicate_repairs(machine, anomaly_id=anomaly.pk)
    from tasks.scope import scoped_work_orders

    visible_ids = set(
        scoped_work_orders(actor)
        .filter(pk__in=[row['work_order_id'] for row in duplicates])
        .values_list('pk', flat=True)
    )
    if any(row['work_order_id'] not in visible_ids for row in duplicates):
        raise WorkPackageError('Existing work needs review by an authorized planner.')
    return {
        'context': context,
        'provider': provider_status(),
        'can_create': not owned and anomaly.is_active,
        'duplicates': duplicates,
        'prepared_scenario': prepared_scenario(machine, anomaly, target),
    }


def make_ai_draft(actor, machine, anomaly_id):
    """Sign the reviewed source and real inference provenance without any Work write."""
    from .intake_binding import draft_fields

    intake = intake_context(actor, machine, anomaly_id)
    if not intake['can_create']:
        raise WorkPackageError('This event is ended or already has owning work.')
    context = intake['context']
    if intake['prepared_scenario']:
        context = {
            **context,
            'prepared_specification_not_field_evidence': intake['prepared_scenario'],
        }
    fields, provenance = draft_fields(context, actor_id=actor.pk)
    claims = {
        'owner_id': actor.pk,
        'machine_id': machine.pk,
        'anomaly_id': anomaly_id,
        'fields': fields,
        'provider': provenance,
        'generated_at': timezone.now().isoformat(),
    }
    return {
        'fields': fields,
        'provider': provenance,
        'review_required': True,
        'generated_at': claims['generated_at'],
        'draft_token': signing.dumps(claims, salt=DRAFT_SALT, compress=True),
    }


def reviewed_ai_draft(actor, draft):
    """Verify provenance and source; human edits are attributed, never model commands."""
    token = draft['source']['ai_draft_token']
    if not token:
        return None
    try:
        claims = signing.loads(token, salt=DRAFT_SALT, max_age=900)
    except signing.BadSignature as exc:
        raise WorkPackageError(
            'The AI draft expired or is invalid. Draft again or continue manually.'
        ) from exc
    if (
        claims['owner_id'] != actor.pk
        or claims['machine_id'] != draft['machine_id']
        or claims['anomaly_id'] != draft['source']['anomaly_id']
    ):
        raise WorkPackageError(
            'The AI draft does not belong to this actor, machine and event.'
        )
    accepted = {
        'title': draft['title'],
        'work_scope': draft['description'],
        'symptom': draft['fault']['symptom'],
        'production_impact': draft['fault']['production_impact'],
    }
    return {
        'provider': claims['provider'],
        'generated_at': claims['generated_at'],
        'reviewed_by': actor.pk,
        'reviewed_at': timezone.now().isoformat(),
        'accepted_fields': accepted,
        'edited_fields': [
            name for name in accepted if accepted[name] != claims['fields'][name]
        ],
    }
