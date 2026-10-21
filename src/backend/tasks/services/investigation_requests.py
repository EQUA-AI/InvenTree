"""Scoped, versioned and idempotent investigation commands; no lifecycle change."""

import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max, Prefetch
from django.utils import timezone

from rest_framework.exceptions import ValidationError

from tasks.investigation_models import InvestigationArtifact, InvestigationRequest
from tasks.investigation_serializers import (
    ArtifactFieldsSerializer,
    InvestigationArtifactSerializer,
    InvestigationRequestRevisionSerializer,
    InvestigationRequestSerializer,
    RequestFieldsSerializer,
    RequiredArtifactSerializer,
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


def _validate_links(fields, work_order, actor):
    from repair.models import RepairInvestigationFinding
    from repair.scope import scoped_repair_packets

    _linked_person(fields.get('owner_id'), work_order)
    # O24: shared Work is not packet authority. Source links need the packet's
    # machine scope and exact packet/Work machine binding, just like reads.
    packets = scoped_repair_packets(actor).filter(work_order=work_order)
    if (
        fields.get('source_finding_id')
        and not RepairInvestigationFinding.objects.filter(
            pk=fields['source_finding_id'], packet__in=packets
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
        packet = packets.filter(pk=proposal['packet_id']).first()
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
        return _response(replay.metadata)
    if type(expected_version) is not int:
        raise ValidationError('An integer Work version is required.')
    _require_version(work, expected_version)
    row = None
    if request_id is not None:
        row = (
            InvestigationRequest.objects
            .select_related('owner')
            .filter(pk=request_id, work_order=work)
            .first()
        )
        if row is None:
            raise ValidationError('Request is unavailable in this Work.')
        if type(request_version) is not int or request_version != row.version:
            raise StaleVersion(
                'Investigation request changed; refresh before retrying.'
            )
    version_before = row.version if row else None
    revision_before = None
    artifact_through_id = None
    if operation in ('revise', 'escalate') and row is not None:
        revision_before = dict(InvestigationRequestRevisionSerializer(row).data)
        artifact_through_id = row.artifacts.aggregate(last=Max('pk'))['last']
    artifact_id = None
    if operation == 'escalate' and row is not None:
        from django.contrib.auth.models import Group

        from rest_framework import serializers

        if not isinstance(fields, dict) or set(fields) != {
            'owner_id',
            'next_checkpoint',
            'blocked_reason',
        }:
            raise ValidationError(
                'Escalation requires exactly one owner, checkpoint and reason.'
            )
        if row.status == 'resolved':
            raise ValidationError('A resolved request cannot be escalated.')
        owner_id = serializers.IntegerField(min_value=1).run_validation(
            fields['owner_id']
        )
        checkpoint = serializers.DateTimeField().run_validation(
            fields['next_checkpoint']
        )
        reason = serializers.CharField(allow_blank=False).run_validation(
            fields['blocked_reason']
        )
        group = Group.objects.filter(
            name=getattr(settings, 'AIMMS_EXPERT_GROUP_NAME', 'Reliability engineers')
        ).first()
        if (
            group is None
            or not group.user_set.filter(pk=owner_id, is_active=True).exists()
        ):
            raise ValidationError(
                'Choose one active member of the configured expert group.'
            )
        _linked_person(owner_id, work)
        if checkpoint <= timezone.now():
            raise ValidationError('Choose a future checkpoint.')
        row.owner_id = owner_id
        row.next_checkpoint = checkpoint
        row.escalation_reason = reason
        row.escalation_group = group
        row.escalated_by = actor
        row.escalated_at = timezone.now()
        row.version += 1
        row.updated_by = actor
        row.save()
    elif operation in ('create', 'revise'):
        if (operation == 'create') != (row is None):
            raise ValidationError('Create needs no request; revise needs a request.')
        validator = RequestFieldsSerializer(data=fields, partial=operation == 'revise')
        validator.is_valid(raise_exception=True)
        clean = validator.validated_data
        _validate_links(clean, work, actor)
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
        if row.escalation_group_id:
            if (
                row.owner_id is None
                or row.next_checkpoint is None
                or not row.escalation_group.user_set.filter(
                    pk=row.owner_id, is_active=True
                ).exists()
            ):
                raise ValidationError(
                    'Escalation must retain one group owner and checkpoint.'
                )
        if clean.get('status') == 'open':
            row.blocked_reason = ''
        if row.status == 'resolved':
            required_validator = RequiredArtifactSerializer(
                data=row.required_artifacts, many=True
            )
            required_validator.is_valid(raise_exception=True)
            artifacts = list(row.artifacts.all()) if row.pk else []
            superseded = {a.supersedes_id for a in artifacts if a.supersedes_id}
            current = [a for a in artifacts if a.pk not in superseded]
            for required in row.required_artifacts:
                candidates = [a for a in current if a.kind == required.get('kind')]
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
                            r.get('value') is not None
                            and (not isinstance(r['value'], str) or r['value'].strip())
                            for r in a.payload.get(key, [])
                        )
                    ]
                if not candidates:
                    raise ValidationError(
                        'Required typed evidence is still missing or unresolved.'
                    )
                current.remove(candidates[0])
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
            attachment = attachments.first()
            if attachment is None:
                raise ValidationError('Attachment must be saved on this Work.')
        supersedes_id = clean.get('supersedes_id')
        if supersedes_id and not row.artifacts.filter(pk=supersedes_id).exists():
            raise ValidationError(
                'Correction must refer to an artifact of this request.'
            )
        if attachment_id:
            clean['attachment_snapshot'] = {
                'id': attachment.pk,
                'filename': attachment.attachment.name,
                'link': attachment.link,
                'comment': attachment.comment,
            }
        artifact = InvestigationArtifact.objects.create(
            request=row, recorded_by=actor, **clean, **(provenance or {})
        )
        payload = clean['payload']
        retain_recording = clean['kind'] == 'observation' or any(
            key in payload for key in ('recording_started_at', 'recording_duration_ms')
        )
        if attachment_id and attachment.attachment and retain_recording:
            from django.utils.dateparse import parse_datetime

            from tasks.services.capture_originals import retain_original

            try:
                with attachment.attachment.open('rb') as original_file:
                    retain_original(
                        work_order=work,
                        actor=actor,
                        artifact=artifact,
                        file=original_file,
                        content_type='',
                        started_at=parse_datetime(payload['recording_started_at'])
                        if payload.get('recording_started_at')
                        else None,
                        duration_ms=payload.get('recording_duration_ms'),
                    )
            except (ValueError, OSError) as exc:
                raise ValidationError(
                    'Original media could not be retained. Check the source file and upload limit.'
                ) from exc
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
    metadata = {
        'request_id': row.pk,
        'work_order_id': work.pk,
        'operation': operation,
        'idempotency_key': idempotency_key,
        'expected_version': expected_version,
        'version_before': version_before,
        'version_after': row.version,
        'status': row.status,
    }
    if operation == 'escalate':
        metadata['escalation'] = {
            'group_id': row.escalation_group_id,
            'group': row.escalation_group.name,
            'owner_id': row.owner_id,
            'next_checkpoint': row.next_checkpoint.isoformat(),
            'recorded_by': actor.pk,
            'recorded_at': row.escalated_at.isoformat(),
            'reason': row.escalation_reason,
        }
    if artifact_id is not None:
        metadata['artifact_id'] = artifact_id
    if revision_before is not None:
        metadata['request_revision'] = {
            'before': revision_before,
            'after': dict(InvestigationRequestRevisionSerializer(row).data),
            'artifact_through_id': artifact_through_id,
        }
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
    return _response(metadata)


def request_queryset():
    """Bounded queries for full projections, independent of artifact count."""
    return InvestigationRequest.objects.select_related(
        'owner', 'updated_by', 'created_by', 'escalation_group', 'escalated_by'
    ).prefetch_related(
        Prefetch(
            'artifacts',
            queryset=InvestigationArtifact.objects.select_related(
                'recorded_by'
            ).prefetch_related('originals'),
        )
    )


def _response(metadata):
    """Hydrate current state separately from the small durable command receipt."""
    row = request_queryset().get(pk=metadata['request_id'])
    result = {**metadata, 'request': dict(InvestigationRequestSerializer(row).data)}
    if metadata.get('artifact_id'):
        result['artifact'] = dict(
            InvestigationArtifactSerializer(
                next(a for a in row.artifacts.all() if a.pk == metadata['artifact_id'])
            ).data
        )
    return result


def project_requests(work_order):
    """A successful empty queue is distinct from an unavailable provider."""
    rows = request_queryset().filter(work_order=work_order)
    return {
        'available': True,
        'reason': '',
        'items': InvestigationRequestSerializer(rows, many=True).data,
    }


def escalation_options(work, actor):
    """Only currently authorized native Work readers are offered as owners."""
    from django.contrib.auth.models import Group

    from tasks.work_execution.common import person

    _change_actor(actor)
    name = getattr(settings, 'AIMMS_EXPERT_GROUP_NAME', 'Reliability engineers')
    group = Group.objects.filter(name=name).first()
    owners = []
    if group:
        for candidate in group.user_set.filter(is_active=True).order_by('pk'):
            try:
                _linked_person(candidate.pk, work)
            except ValidationError:
                continue
            owners.append({'id': candidate.pk, 'name': person(candidate)})
    return {
        'group': name,
        'available': group is not None,
        'reason': '' if group else 'group_not_configured',
        'owners': owners,
    }
