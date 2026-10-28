"""Scoped prepared evidence, readings, and exercise verification commands."""

import hashlib
import uuid
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import transaction

from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import Attachment
from repair.pump08_exercise import (
    DEFAULT_EVIDENCE_PROFILE,
    EVIDENCE,
    EVIDENCE_PROFILES,
    TECH_A_V9_EVIDENCE_PROFILE,
    evidence_bytes,
)
from tasks.permissions import EXECUTE_WORKORDER, require_permission
from tasks.procedure_execution_api import (
    ProcedureExecutionEnabledMixin,
    _scoped_work_order,
)
from tasks.services.procedure_execution import (
    ProcedureExecutionError,
    _command_replay,
    _record_command,
    complete_step,
)
from tasks.services.work_orders import (
    IdempotencyConflict,
    StaleVersion,
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

    def post(self, request, pk):
        """Record the two prepared readings with native execution authority."""
        return _record_exercise_readings_response(request, pk)


@transaction.atomic
def attach_prepared_evidence(
    work_order_id, actor, idempotency_key, *, profile=DEFAULT_EVIDENCE_PROFILE
):
    """Explicitly attach prepared sheets, not signed field or physical evidence."""
    if profile not in EVIDENCE_PROFILES:
        raise ValidationError('Unknown prepared evidence profile.')
    work = _locked_work_order(work_order_id)
    _require_scope(actor, work)
    require_permission(actor, EXECUTE_WORKORDER)
    source = exercise_source(work)
    if not work.is_active or source is None:
        raise ValidationError(
            'This active work order does not own the Pump08 exercise source.'
        )
    digest = _canonical_hash('exercise_evidence', actor, {'profile': profile})
    replay = _replay_or_none(work, idempotency_key, digest)
    if replay:
        return project(work, actor)
    if work.events.filter(event_type='EXERCISE_EVIDENCE_ATTACHED').exists():
        raise ValidationError(
            'Prepared evidence is already attached. Review the existing records.'
        )
    if profile == TECH_A_V9_EVIDENCE_PROFILE and (
        source.status not in {'open', 'acknowledged'}
        or source.severity != 'critical'
        or work.actual_started_at is not None
        or work.lifecycle_status in {'completed', 'canceled', 'verifying'}
    ):
        raise ValidationError(
            'The V9 prepared profile requires a fresh critical exercise case.'
        )
    evidence = {}
    for code in EVIDENCE:
        content = evidence_bytes(code, profile=profile)
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
        result_metadata={'profile': profile, 'evidence': evidence},
    )
    return project(work, actor)


class ExerciseEvidence(ExerciseVerification):
    """An explicit authorized action prepares the three labelled evidence sheets."""

    def post(self, request, pk):
        """Attach once, atomically, with the actual actor and native command receipt."""
        work = _scoped_work_order(request, pk)
        data = object_data(request.data)
        key = serializers.CharField(max_length=128, allow_blank=False).run_validation(
            data.get('idempotency_key')
        )
        profile = serializers.ChoiceField(choices=EVIDENCE_PROFILES).run_validation(
            data.get('profile', DEFAULT_EVIDENCE_PROFILE)
        )
        try:
            result = attach_prepared_evidence(
                work.pk, request.user, key, profile=profile
            )
        except PermissionDenied as exc:
            return Response({'detail': str(exc)}, status=403)
        except IdempotencyConflict as exc:
            return Response(
                {'detail': str(exc), 'code': 'IDEMPOTENCY_CONFLICT'}, status=409
            )
        return Response({**result, 'idempotency_key': key})


class ExerciseReadingsSerializer(serializers.Serializer):
    """Two typed prepared observations for one governed exercise check."""

    idempotency_key = serializers.CharField(max_length=128, allow_blank=False)
    application_id = serializers.IntegerField(min_value=1)
    step_key = serializers.UUIDField()
    expected_version = serializers.IntegerField(min_value=1)
    winding_5 = serializers.DecimalField(max_digits=8, decimal_places=1)
    winding_6 = serializers.DecimalField(max_digits=8, decimal_places=1)
    maximum = serializers.DecimalField(max_digits=8, decimal_places=1, required=False)
    evidence_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), allow_empty=False
    )
    note = serializers.CharField(max_length=4000, allow_blank=False)

    def to_internal_value(self, data):
        """Reject extra result fields instead of silently ignoring a client maximum."""
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError({
                'non_field_errors': ['Unexpected exercise reading fields.']
            })
        return super().to_internal_value(data)

    def validate(self, data):
        """A client may check its expectation but cannot supply the result."""
        if 'maximum' in data and data['maximum'] != max(
            data['winding_5'], data['winding_6']
        ):
            raise serializers.ValidationError(
                'The supplied maximum does not match the two readings.'
            )
        return data


def _reading_decimal(value):
    """Validate the same bounded finite precision at the service boundary."""
    if isinstance(value, bool) or value is None:
        raise ValidationError('Each winding reading must be a finite number.')
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError('Each winding reading must be a finite number.') from exc
    if (
        not number.is_finite()
        or number.as_tuple().exponent < -1
        or abs(number) >= Decimal('10000000')
    ):
        raise ValidationError(
            'Each winding reading must be finite with at most one decimal place.'
        )
    return number


@transaction.atomic
def record_exercise_readings(
    *,
    work_order_id,
    actor,
    idempotency_key,
    application_id,
    step_key,
    expected_version,
    winding_5,
    winding_6,
    evidence_ids,
    note,
    maximum=None,
):
    """Record W5 and W6 as distinct audit events and derive the native step result."""
    work = _locked_work_order(work_order_id)
    _require_scope(actor, work)
    require_permission(actor, EXECUTE_WORKORDER)
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or len(idempotency_key) > 128
    ):
        raise ValidationError('A valid idempotency key is required.')
    winding_5 = _reading_decimal(winding_5)
    winding_6 = _reading_decimal(winding_6)
    maximum = _reading_decimal(maximum) if maximum is not None else None
    payload = {
        'application_id': application_id,
        'step_key': str(step_key),
        'expected_version': expected_version,
        'winding_5': str(winding_5),
        'winding_6': str(winding_6),
        'evidence_ids': evidence_ids,
        'note': note,
        'maximum': str(maximum) if maximum is not None else None,
    }
    digest = _canonical_hash('exercise_readings', actor, payload)
    replay = _command_replay(work, 'exercise_readings', idempotency_key, digest)
    if replay:
        return replay.metadata
    state = project(work, actor)
    if (
        not work.is_active
        or not state.get('applicable')
        or state.get('application_id') != application_id
        or state.get('reasons')
        or len(state.get('checks', [])) != 3
    ):
        raise ValidationError(
            'The current source and published exercise revision must be valid.'
        )
    check = state['checks'][1]
    if check['sequence'] != 2 or check['step_key'] != str(step_key):
        raise ValidationError('The readings do not belong to this exercise check.')
    if check['version'] != expected_version:
        raise StaleVersion('The exercise check changed. Reload it before recording.')
    if 'criteria_invalid' in check['reasons']:
        raise ValidationError('The exercise check criteria are invalid.')
    if (
        not set(check['suggested_evidence_ids']).issubset(evidence_ids)
        or not check['suggested_evidence_ids']
    ):
        raise ValidationError('The current prepared E06 evidence is required.')
    derived = max(winding_5, winding_6)
    if maximum is not None and maximum != derived:
        raise ValidationError('The supplied maximum does not match the two readings.')
    correlation = uuid.uuid4()
    execution = complete_step(
        work_order_id=work.pk,
        application_id=application_id,
        step_key=step_key,
        actor=actor,
        expected_version=expected_version,
        idempotency_key=f'exercise-step-{uuid.uuid4()}',
        value={'number': str(derived)},
        note=note,
        evidence_ids=evidence_ids,
        correlation_id=correlation,
    )
    common = {
        'application_id': application_id,
        'step_execution_id': execution.pk,
        'step_key': str(step_key),
        'version': execution.version,
        'evidence_id': check['suggested_evidence_ids'][0],
    }
    reading_event_ids = {}
    for channel, value in (('W5', winding_5), ('W6', winding_6)):
        event = _record_command(
            work_order=work,
            actor=actor,
            command='exercise_reading',
            event_type='EXERCISE_READING_RECORDED',
            idempotency_key=f'exercise-reading-{uuid.uuid4()}',
            request_hash=_canonical_hash(
                'exercise_reading',
                actor,
                {**common, 'channel': channel, 'value': str(value)},
            ),
            correlation_id=correlation,
            metadata={**common, 'channel': channel, 'value': str(value)},
        )
        reading_event_ids[channel] = event.pk
    summary = _record_command(
        work_order=work,
        actor=actor,
        command='exercise_readings',
        event_type='EXERCISE_READINGS_RECORDED',
        idempotency_key=idempotency_key,
        request_hash=digest,
        correlation_id=correlation,
        metadata={
            **common,
            'reading_event_ids': reading_event_ids,
            'maximum': str(derived),
        },
    )
    return summary.metadata


def _record_exercise_readings_response(request, pk):
    """Return the server-derived result and its durable step version."""
    work = _scoped_work_order(request, pk)
    serializer = ExerciseReadingsSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        result = record_exercise_readings(
            work_order_id=work.pk, actor=request.user, **serializer.validated_data
        )
    except PermissionDenied as exc:
        return Response({'detail': str(exc)}, status=403)
    except (IdempotencyConflict, StaleVersion) as exc:
        return Response(
            {'detail': str(exc), 'code': getattr(exc, 'code', 'CONFLICT')}, status=409
        )
    except ProcedureExecutionError as exc:
        return Response({'detail': str(exc), 'code': exc.code}, status=400)
    return Response({
        **result,
        'work_order_id': work.pk,
        'idempotency_key': serializer.validated_data['idempotency_key'],
    })
