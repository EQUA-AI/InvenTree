"""Exercise evidence and read-only verdict; native procedure writes stay separate."""

import hashlib
import uuid

from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import transaction

from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import Attachment
from repair.pump08_exercise import EVIDENCE, evidence_bytes
from tasks.permissions import EXECUTE_WORKORDER, require_permission
from tasks.procedure_execution_api import (
    ProcedureExecutionEnabledMixin,
    _scoped_work_order,
)
from tasks.services.work_orders import (
    IdempotencyConflict,
    _append_result,
    _canonical_hash,
    _locked_work_order,
    _replay_or_none,
    _require_scope,
)
from tasks.work_execution.exercise_verification import exercise_source, project
from tasks.workorder_mutations import object_data


class ExerciseVerification(ProcedureExecutionEnabledMixin, APIView):
    """Read a source/revision/evidence-bound software-only exercise verdict."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        """GET is pure: no authority, Work state, evidence or telemetry writes."""
        work = _scoped_work_order(request, pk)
        response = Response(project(work, request.user))
        response['Cache-Control'] = 'no-store'
        return response


@transaction.atomic
def attach_prepared_evidence(work_order_id, actor, idempotency_key):
    """Explicitly attach prepared sheets, not signed field or physical evidence."""
    work = _locked_work_order(work_order_id)
    _require_scope(actor, work)
    require_permission(actor, EXECUTE_WORKORDER)
    if not work.is_active or exercise_source(work) is None:
        raise ValidationError(
            'This active work order does not own the Pump08 exercise source.'
        )
    digest = _canonical_hash(
        'exercise_evidence', actor, {'profile': 'P08-MOTOR-COOL-RTS-EX'}
    )
    replay = _replay_or_none(work, idempotency_key, digest)
    if replay:
        return project(work, actor)
    if work.events.filter(event_type='EXERCISE_EVIDENCE_ATTACHED').exists():
        raise ValidationError(
            'Prepared evidence is already attached. Review the existing records.'
        )
    evidence = {}
    for code in EVIDENCE:
        content = evidence_bytes(code)
        attachment = Attachment.objects.create(
            model_type='kanbancard',
            model_id=work.pk,
            upload_user=actor,
            comment=f'{code} | Prepared exercise evidence; not physical work',
            attachment=ContentFile(content, name=f'{code}.md'),
        )
        evidence[code] = {
            'id': attachment.pk,
            'sha256': hashlib.sha256(content).hexdigest(),
        }
    _append_result(
        work_order=work,
        actor=actor,
        command='exercise_evidence',
        event_type='EXERCISE_EVIDENCE_ATTACHED',
        from_status=work.lifecycle_status,
        to_status=work.lifecycle_status,
        reason='Prepared exercise sheets attached; no physical execution asserted.',
        correlation_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=digest,
        result_metadata={'profile': 'P08-MOTOR-COOL-RTS-EX', 'evidence': evidence},
    )
    return project(work, actor)


class ExerciseEvidence(ExerciseVerification):
    """An explicit authorized action prepares the three labelled evidence sheets."""

    def post(self, request, pk):
        """Attach once, atomically, with the actual actor and native command receipt."""
        work = _scoped_work_order(request, pk)
        key = serializers.CharField(max_length=128, allow_blank=False).run_validation(
            object_data(request.data).get('idempotency_key')
        )
        try:
            result = attach_prepared_evidence(work.pk, request.user, key)
        except PermissionDenied as exc:
            return Response({'detail': str(exc)}, status=403)
        except IdempotencyConflict as exc:
            return Response(
                {'detail': str(exc), 'code': 'IDEMPOTENCY_CONFLICT'}, status=409
            )
        return Response({**result, 'idempotency_key': key})
