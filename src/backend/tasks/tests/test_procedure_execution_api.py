"""API contract tests for governed procedure execution."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIClient

from company.models import Company
from tasks.models import (
    KanbanCard,
    Procedure,
    ProcedureRevision,
    ProcedureRevisionStatus,
    ProcedureStep,
    ProcedureStepType,
    StepExecutionStatus,
    WorkOrderDeviation,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope


@override_settings(AIMMS_PROCEDURES_ENABLED=True)
class ProcedureExecutionAPITest(TestCase):
    """Exercise scoped application, step commands, and deviations."""

    def setUp(self):
        """Create one published procedure and an explicitly scoped actor."""
        self.customer = Company.objects.create(
            name='Execution API Customer', is_customer=True
        )
        self.other_customer = Company.objects.create(
            name='Other Execution Customer', is_customer=True
        )
        self.actor = get_user_model().objects.create_superuser(
            username='execution-api-supervisor',
            email='execution-api@example.com',
            password='test-password',
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.work_order = KanbanCard.objects.create(
            title='Execute standard work',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM,
            customer=self.customer,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.procedure = Procedure.objects.create(
            code='EXEC-API',
            name='Execution API Procedure',
            customer=self.customer,
            created_by=self.actor,
        )
        self.revision = ProcedureRevision.objects.create(
            procedure=self.procedure,
            revision=1,
            status=ProcedureRevisionStatus.PUBLISHED,
            work_order_type=WorkOrderType.PREVENTIVE,
            created_by=self.actor,
            published_by=self.actor,
            published_at=timezone.now(),
        )
        self.procedure.current_revision = self.revision
        self.procedure.save(update_fields=['current_revision'])
        self.step = ProcedureStep.objects.create(
            revision=self.revision,
            sequence=1,
            step_type=ProcedureStepType.MEASUREMENT,
            title='Measure voltage',
            instruction='Measure supply voltage.',
            value_type='number',
            min_value='10',
            max_value='20',
        )
        self.second_step = ProcedureStep.objects.create(
            revision=self.revision,
            sequence=2,
            step_type=ProcedureStepType.INSTRUCTION,
            title='Record condition',
            instruction='Record the final condition.',
            value_type='none',
        )

    def apply(self, idempotency_key='execution-api-apply'):
        """Apply the fixture revision through the public endpoint."""
        return self.client.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/procedures/apply/',
            {
                'revision_id': self.revision.pk,
                'expected_version': self.work_order.lifecycle_version,
                'idempotency_key': idempotency_key,
            },
            format='json',
        )

    def test_apply_and_read_exact_ordered_snapshot(self):
        """Apply delegates to the service and exposes its immutable snapshot."""
        lifecycle = self.work_order.lifecycle_status
        lifecycle_version = self.work_order.lifecycle_version
        response = self.apply()
        replay = self.apply()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(replay.status_code, status.HTTP_201_CREATED)
        self.assertEqual(replay.json(), response.json())
        application = response.json()
        self.assertEqual(application['revision'], self.revision.pk)
        self.assertEqual(application['work_order'], self.work_order.pk)
        self.assertEqual(application['drift_status'], 'current')
        self.assertEqual(application['snapshot']['steps'][0]['key'], str(self.step.key))
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, lifecycle)
        self.assertEqual(self.work_order.lifecycle_version, lifecycle_version)

        applications = self.client.get(
            f'/api/tasks/work-orders/{self.work_order.pk}/procedures/'
        ).json()
        executions = self.client.get(
            f'/api/tasks/work-orders/{self.work_order.pk}/steps/'
        ).json()
        self.assertEqual(applications['count'], 1)
        self.assertEqual(executions['count'], 2)
        self.assertEqual(
            [row['step_key'] for row in executions['results']],
            [str(self.step.key), str(self.second_step.key)],
        )
        execution = executions['results'][0]
        self.assertEqual(execution['step_key'], str(self.step.key))
        self.assertEqual(
            execution['step_snapshot']['instruction'], 'Measure supply voltage.'
        )

    def test_complete_and_reopen_use_step_version(self):
        """Step commands return service-owned state and optimistic versions."""
        self.apply()
        complete = self.client.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/steps/{self.step.key}/complete/',
            {
                'expected_version': 1,
                'idempotency_key': 'execution-api-complete',
                'value': {'number': '25'},
                'passed': True,
            },
            format='json',
        )
        self.assertEqual(complete.status_code, status.HTTP_200_OK)
        self.assertEqual(complete.json()['status'], StepExecutionStatus.FAILED)
        self.assertEqual(complete.json()['value'], {'number': '25'})
        self.assertEqual(complete.json()['version'], 2)

        reopen = self.client.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/steps/{self.step.key}/reopen/',
            {
                'expected_version': 2,
                'idempotency_key': 'execution-api-reopen',
                'reason': 'Repeat after calibration',
            },
            format='json',
        )
        self.assertEqual(reopen.status_code, status.HTTP_200_OK)
        self.assertEqual(reopen.json()['status'], StepExecutionStatus.PENDING)
        self.assertEqual(reopen.json()['version'], 3)

    def test_required_step_needs_result_or_explicit_disposition(self):
        """A required step cannot be silently skipped through complete."""
        self.apply()
        complete = self.client.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/steps/{self.step.key}/complete/',
            {'expected_version': 1, 'idempotency_key': 'execution-api-silent-skip'},
            format='json',
        )
        self.assertEqual(complete.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(complete.json()['code'], 'STEP_REQUIRED')

        disposition = self.client.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/steps/{self.step.key}/not-applicable/',
            {
                'expected_version': 1,
                'idempotency_key': 'execution-api-not-applicable',
                'reason': 'Asset option is not installed',
            },
            format='json',
        )
        self.assertEqual(disposition.status_code, status.HTTP_200_OK)
        self.assertEqual(
            disposition.json()['status'], StepExecutionStatus.NOT_APPLICABLE
        )
        deviation = WorkOrderDeviation.objects.get(category='step_not_applicable')
        self.assertEqual(deviation.reason, 'Asset option is not installed')
        self.assertEqual(deviation.step_key, str(self.step.key))

    def test_stale_step_returns_stable_conflict_envelope(self):
        """Stale execution commands report the current step token."""
        self.apply()
        response = self.client.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/steps/{self.step.key}/complete/',
            {
                'expected_version': 2,
                'idempotency_key': 'execution-api-stale',
                'value': {'number': '15'},
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.json()['code'], 'STALE_VERSION')
        self.assertEqual(response.json()['current_version'], 1)
        self.assertEqual(response.json()['blockers'], [])

    def test_deviation_create_owns_actor_and_parent(self):
        """Clients cannot select deviation ownership or resolution state."""
        response = self.client.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/deviations/',
            {
                'category': 'field_condition',
                'reason': 'Unexpected enclosure damage',
                'work_order': 999999,
                'actor': None,
                'resolution': 'client supplied',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        deviation = WorkOrderDeviation.objects.get(pk=response.json()['id'])
        self.assertEqual(deviation.work_order, self.work_order)
        self.assertEqual(deviation.actor, self.actor)
        self.assertEqual(deviation.resolution, '')

        rows = self.client.get(
            f'/api/tasks/work-orders/{self.work_order.pk}/deviations/'
        ).json()
        self.assertEqual(rows['count'], 1)

    def test_cross_scope_parent_and_revision_return_not_found(self):
        """Neither parent nor revision child lookup leaks another customer."""
        hidden_order = KanbanCard.objects.create(
            title='Hidden work',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM,
            customer=self.other_customer,
        )
        parent = self.client.get(f'/api/tasks/work-orders/{hidden_order.pk}/steps/')
        self.assertEqual(parent.status_code, status.HTTP_404_NOT_FOUND)

        hidden_procedure = Procedure.objects.create(
            code='HIDDEN-EXEC',
            name='Hidden procedure',
            customer=self.other_customer,
            created_by=self.actor,
        )
        hidden_revision = ProcedureRevision.objects.create(
            procedure=hidden_procedure,
            revision=1,
            work_order_type=WorkOrderType.PREVENTIVE,
            created_by=self.actor,
        )
        revision = self.client.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/procedures/apply/',
            {
                'revision_id': hidden_revision.pk,
                'expected_version': 1,
                'idempotency_key': 'hidden-revision',
            },
            format='json',
        )
        self.assertEqual(revision.status_code, status.HTTP_404_NOT_FOUND)
