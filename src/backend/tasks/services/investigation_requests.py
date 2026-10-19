"""Scoped, versioned and idempotent investigation commands; no lifecycle change."""

import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction

from rest_framework.exceptions import ValidationError

from tasks.investigation_models import InvestigationArtifact, InvestigationRequest
from tasks.investigation_serializers import (
    ArtifactFieldsSerializer,
    InvestigationRequestSerializer,
    RequestFieldsSerializer,
)
from tasks.scope import scoped_work_orders
from tasks.services.work_orders import (
    StaleVersion,
    _append_result,
    _canonical_hash,
    _locked_work_order,
    _replay_or_none,
    _require_scope,
    _require_version,
)
from users.models import RuleSet


def _change_actor(actor):
    live = (
        get_user_model()
        .objects.filter(pk=getattr(actor, 'pk', None), is_active=True)
        .first()
    )
    if live is None or not (
        live.is_superuser
        or RuleSet.objects.filter(
            group__user=live, name='work_order', can_change=True, can_view=True
        ).exists()
    ):
        raise PermissionDenied('Work change permission required.')
    return live


def _linked_person(actor_id, work_order):
    if actor_id is None:
        return
    owner = get_user_model().objects.filter(pk=actor_id, is_active=True).first()
    if owner is None or not scoped_work_orders(owner).filter(pk=work_order.pk).exists():
        raise ValidationError('Owner must currently have access to this Work.')
    if not (
        owner.is_superuser
        or RuleSet.objects.filter(
            group__user=owner, name='work_order', can_view=True
        ).exists()
    ):
        raise ValidationError('Owner must have the native Work view role.')


def _validate_links(fields, work_order):
    from repair.models import RepairInvestigationFinding, RepairPacket

    _linked_person(fields.get('owner_id'), work_order)
    if (
        fields.get('source_finding_id')
        and not RepairInvestigationFinding.objects.filter(
            pk=fields['source_finding_id'], packet__work_order=work_order
        ).exists()
    ):
        raise ValidationError('Finding must belong to this Work.')
    proposal = fields.get('source_proposal')
    if proposal is not None:
        if not isinstance(proposal, dict) or set(proposal) != {
            'packet_id',
            'check',
            'generated_at',
        }:
            raise ValidationError(
                'Proposal requires packet_id, check and generated_at.'
            )
        packet = RepairPacket.objects.filter(
            work_order=work_order, pk=proposal['packet_id']
        ).first()
        if (
            packet is None
            or proposal['check']
            not in (packet.diagnosis or {}).get('confirm_tests', [])
            or proposal['generated_at'] != (packet.diagnosis or {}).get('generated_at')
        ):
            raise ValidationError('Proposal must match this Work diagnosis revision.')


@transaction.atomic
def command(
    *,
    work_order_id,
    actor,
    expected_version,
    idempotency_key,
    operation,
    fields,
    request_id=None,
    request_version=None,
    provenance=None,
):
    """Reauthorize even exact retries; audit saved results before returning them."""
    actor = _change_actor(actor)
    work = _locked_work_order(work_order_id)
    _require_scope(actor, work)
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or len(idempotency_key) > 128
    ):
        raise ValidationError(
            'A stable idempotency key of at most 128 characters is required.'
        )
    intent = {
        'operation': operation,
        'fields': fields,
        'request_id': request_id,
        'request_version': request_version,
        'expected_version': expected_version,
        'provenance': provenance,
    }
    request_hash = _canonical_hash('investigation', actor, intent)
    replay = _replay_or_none(work, idempotency_key, request_hash)
    if replay:
        return replay.metadata
    if type(expected_version) is not int:
        raise ValidationError('An integer Work version is required.')
    _require_version(work, expected_version)
    row = None
    if request_id is not None:
        row = InvestigationRequest.objects.filter(
            pk=request_id, work_order=work
        ).first()
        if row is None:
            raise ValidationError('Request is unavailable in this Work.')
        if type(request_version) is not int or request_version != row.version:
            raise StaleVersion(
                'Investigation request changed; refresh before retrying.'
            )
    before = InvestigationRequestSerializer(row).data if row else None
    artifact_id = None
    if operation in ('create', 'revise'):
        if (operation == 'create') != (row is None):
            raise ValidationError('Create needs no request; revise needs a request.')
        validator = RequestFieldsSerializer(data=fields, partial=operation == 'revise')
        validator.is_valid(raise_exception=True)
        clean = validator.validated_data
        _validate_links(clean, work)
        if row is None:
            row = InvestigationRequest(
                work_order=work, created_by=actor, updated_by=actor
            )
        for key, value in clean.items():
            setattr(row, key, value)
        if (
            row.status in ('cannot_perform', 'pending_external', 'disputed')
            and not row.blocked_reason.strip()
        ):
            raise ValidationError('An unresolved outcome requires an explicit reason.')
        if row.status == 'resolved' and (
            not row.resolution.strip() or row.owner_id is None
        ):
            raise ValidationError(
                'Resolution requires an attributed owner and explanation.'
            )
        if row.status == 'resolved':
            artifacts = list(row.artifacts.all()) if row.pk else []
            superseded = {a.supersedes_id for a in artifacts if a.supersedes_id}
            current = [a for a in artifacts if a.pk not in superseded]
            for required in row.required_artifacts:
                candidates = [a for a in current if a.kind == required['kind']]
                if required['kind'] in ('laboratory', 'report'):
                    candidates = [
                        a for a in candidates if a.payload.get('status') == 'final'
                    ]
                if required['kind'] == 'check_result':
                    candidates = [
                        a for a in candidates if a.payload.get('status') == 'performed'
                    ]
                if required['kind'] in ('laboratory', 'reading_set'):
                    key = 'results' if required['kind'] == 'laboratory' else 'readings'
                    candidates = [
                        a
                        for a in candidates
                        if all(
                            r.get('value') is not None for r in a.payload.get(key, [])
                        )
                    ]
                if not candidates:
                    raise ValidationError(
                        'Required typed evidence is still missing or unresolved.'
                    )
        if row.pk:
            row.version += 1
        row.updated_by = actor
        row.save()
    elif operation == 'artifact' and row is not None:
        if row.status == 'resolved':
            raise ValidationError(
                'Reopen this request explicitly before adding evidence.'
            )
        validator = ArtifactFieldsSerializer(data=fields)
        validator.is_valid(raise_exception=True)
        clean = validator.validated_data
        attachment_id = clean.get('attachment_id')
        if attachment_id:
            from common.models import Attachment

            attachments = Attachment.objects.filter(
                pk=attachment_id, model_type='workorder', model_id=work.pk
            )
            if clean['kind'] == 'photo':
                attachments = attachments.filter(is_image=True)
            if not attachments.exists():
                raise ValidationError('Attachment must be saved on this Work.')
        supersedes_id = clean.get('supersedes_id')
        if supersedes_id and not row.artifacts.filter(pk=supersedes_id).exists():
            raise ValidationError(
                'Correction must refer to an artifact of this request.'
            )
        artifact = InvestigationArtifact.objects.create(
            request=row, recorded_by=actor, **clean, **(provenance or {})
        )
        artifact_id = artifact.pk
        # A technician's explicit inability/dispute is an unresolved outcome
        # of this request only. Later readings never silently clear it.
        if clean['kind'] == 'check_result' and clean['payload']['status'] in (
            'cannot_perform',
            'disputed',
            'pending',
        ):
            row.status = {
                'cannot_perform': 'cannot_perform',
                'disputed': 'disputed',
                'pending': 'pending_external',
            }[clean['payload']['status']]
            row.blocked_reason = clean['payload']['text']
        row.version += 1
        row.updated_by = actor
        row.save(
            update_fields=[
                'version',
                'updated_by',
                'updated_at',
                'status',
                'blocked_reason',
            ]
        )
    else:
        raise ValidationError('Unsupported investigation command.')
    result = dict(InvestigationRequestSerializer(row).data)
    metadata = {'request': result, 'before': before}
    if artifact_id is not None:
        metadata['artifact_id'] = artifact_id
    _append_result(
        work_order=work,
        actor=actor,
        command='investigation',
        event_type='investigation_' + operation,
        from_status=work.lifecycle_status,
        to_status=work.lifecycle_status,
        reason='Investigation request updated',
        correlation_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata=metadata,
    )
    return metadata


def project_requests(work_order):
    """A successful empty queue is distinct from an unavailable provider."""
    rows = work_order.investigation_requests.select_related('owner').prefetch_related(
        'artifacts__recorded_by'
    )
    return {
        'available': True,
        'reason': '',
        'items': InvestigationRequestSerializer(rows, many=True).data,
    }
