"""Administrator-only, scope-first test-alarm commands."""

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from rest_framework import serializers
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from tasks.scope import ScopeError
from tasks.services import scheduling

from InvenTree.permissions import InvenTreeTokenMatchesOASRequirements, map_scope
from machine_health.services.test_alarm_work import trigger_with_work, work_plan
from machine_health.services.test_alarms import targets, trigger_test_alarm
from repair.work_packages import DuplicateRepairConflict, WorkPackageError


class TestAlarmSerializer(serializers.Serializer):
    """An exact component/channel selection and explicit test confirmation."""

    binding_id = serializers.IntegerField(min_value=1)
    component_id = serializers.IntegerField(min_value=1, allow_null=True)
    idempotency_key = serializers.UUIDField()
    confirmed = serializers.BooleanField()
    test_value = serializers.FloatField(required=False)
    create_work_order = serializers.BooleanField(default=False)
    event_owned_work = serializers.BooleanField(default=False)
    plan_token = serializers.CharField(max_length=24000, required=False)
    assigned_to_id = serializers.IntegerField(min_value=1, required=False)
    duplicate_override = serializers.BooleanField(default=False)
    duplicate_override_reason = serializers.CharField(
        max_length=500, allow_blank=True, default=''
    )

    def validate(self, data):
        """Validate the distinct event-owned and explicit-preview paths."""
        if data['event_owned_work']:
            if (
                data['create_work_order']
                or data.get('plan_token')
                or data.get('assigned_to_id')
            ):
                raise serializers.ValidationError(
                    'Event-owned work does not accept manual planning fields.'
                )
            if data['duplicate_override'] and not data['duplicate_override_reason']:
                raise serializers.ValidationError(
                    'Explain why a separate work order is needed.'
                )
            if not data['duplicate_override'] and data['duplicate_override_reason']:
                raise serializers.ValidationError(
                    'A separate work order reason requires duplicate review.'
                )
        elif data['create_work_order']:
            if not data.get('plan_token') or not data.get('assigned_to_id'):
                raise serializers.ValidationError(
                    'Review the work preview and select assigned staff.'
                )
            if (
                data['duplicate_override']
                and not data['duplicate_override_reason'].strip()
            ):
                raise serializers.ValidationError(
                    'Explain why a separate work order is needed.'
                )
        elif (
            data.get('plan_token')
            or data.get('assigned_to_id')
            or data['duplicate_override']
        ):
            raise serializers.ValidationError(
                'Work planning fields require automatic work creation.'
            )
        return data

    def validate_confirmed(self, value):
        """Never treat opening the control as permission to raise an alarm."""
        if not value:
            raise serializers.ValidationError('Explicit test confirmation is required.')
        return value


class MachineTestAlarm(APIView):
    """No token read scope or staff-only role can grant test injection."""

    permission_classes = [
        IsAuthenticated,
        IsAdminUser,
        InvenTreeTokenMatchesOASRequirements,
    ]
    required_alternate_scopes = map_scope(roles=['work_order'])

    def machine(self, request, pk):
        """Resolve the native health scope before returning any target details."""
        from machine_health.api import _machine

        if not getattr(settings, 'AIMMS_TEST_ALARMS_ENABLED', False):
            raise Http404
        if not request.user.is_superuser:
            raise PermissionDenied('Only administrators can trigger test alarms.')
        return _machine(request, pk)

    def get(self, request, pk):
        """List only channels actually mapped to the authorized machine."""
        machine = self.machine(request, pk)
        if 'binding_id' in request.query_params:
            binding_id = serializers.IntegerField(min_value=1).run_validation(
                request.query_params['binding_id']
            )
            try:
                response = Response(
                    work_plan(
                        actor=request.user, machine=machine, binding_id=binding_id
                    )
                )
            except WorkPackageError as exc:
                return Response({'code': exc.code, 'detail': str(exc)}, status=400)
            response['Cache-Control'] = 'no-store'
            return response
        return Response({
            'results': targets(machine),
            'event_work_enabled': bool(
                getattr(settings, 'AIMMS_TEST_ALARM_EVENT_WORK_ENABLED', False)
                and getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False)
            ),
        })

    def post(self, request, pk):
        """Raise the selected synthetic condition once per request key."""
        machine = self.machine(request, pk)
        serializer = TestAlarmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.pop('confirmed')
        automatic = data.pop('create_work_order')
        event_owned = data.pop('event_owned_work')
        planning = {
            key: data.pop(key, None)
            for key in (
                'plan_token',
                'assigned_to_id',
                'duplicate_override',
                'duplicate_override_reason',
            )
        }
        work_order = None
        try:
            if event_owned:
                anomaly, work_order = trigger_with_work(
                    machine=machine,
                    actor=request.user,
                    event_owned=True,
                    duplicate_override=planning['duplicate_override'],
                    duplicate_override_reason=planning['duplicate_override_reason'],
                    **data,
                )
            elif automatic:
                anomaly, work_order = trigger_with_work(
                    machine=machine, actor=request.user, **planning, **data
                )
            else:
                anomaly = trigger_test_alarm(
                    machine=machine, actor=request.user, **data
                )
        except ScopeError:
            raise Http404
        except DuplicateRepairConflict as exc:
            return Response(
                {'code': exc.code, 'detail': str(exc), 'duplicates': exc.duplicates},
                status=409,
            )
        except scheduling.IdempotencyConflict as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=409)
        except (WorkPackageError, scheduling.WorkOrderCommandError) as exc:
            return Response({'code': exc.code, 'detail': str(exc)}, status=400)
        except ValidationError as exc:
            return Response({'detail': exc.messages}, status=400)
        receipt = {
            'anomaly_id': anomaly.pk,
            'idempotency_key': str(data['idempotency_key']),
            'binding_id': anomaly.metrics['binding_id'],
            'machine_id': machine.pk,
            'status': anomaly.status,
            'synthetic': True,
            'test_value': anomaly.metrics['test_value'],
            'unit': anomaly.metrics['unit'],
            'notification': 'Queued through configured notification channels',
        }
        if automatic or event_owned:
            receipt['work_order'] = work_order
        return Response(receipt)
