"""P51: scoped routine records preserve their original type, values and owner."""

import uuid
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from assets.models import AssetMachine, get_default_client
from InvenTree.unit_test import InvenTreeAPITestCase
from tasks.closeout_models import CloseoutReading
from tasks.models import (
    Procedure,
    ProcedureRevision,
    WorkOrder,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
)
from tasks.work_execution.routine import project


@override_settings(
    AIMMS_PROCEDURES_ENABLED=True,
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_CLOSEOUT_WIZARD_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
)
class RoutineProjectionTest(InvenTreeAPITestCase):
    """Read the real overview and existing models, without a new writer."""

    roles = 'all'

    def setUp(self):
        """Create snapshotted tasks and original before/after measurements."""
        super().setUp()
        machine = AssetMachine.objects.create(
            name='Routine fixture', client=get_default_client()
        )
        self.work = WorkOrder.objects.create(
            title='Calibration fixture', machine=machine, work_order_type='calibration'
        )
        procedure = Procedure.objects.create(
            code='ROUTINE', name='Routine fixture', created_by=self.user
        )
        revision = ProcedureRevision.objects.create(
            procedure=procedure,
            revision=1,
            work_order_type='calibration',
            created_by=self.user,
        )
        self.application = WorkOrderProcedureApplication.objects.create(
            work_order=self.work,
            revision=revision,
            snapshot={'revision': {'revision': 1}},
            snapshot_hash='a' * 64,
            applied_by=self.user,
            idempotency_key='routine-fixture',
        )
        self.execution = WorkOrderStepExecution.objects.create(
            application=self.application,
            step_key=uuid.uuid4(),
            sequence=1,
            step_snapshot={'title': 'Original instruction', 'step_type': 'instruction'},
            status='completed',
            value={'boolean': False},
            completed_by=self.user,
            completed_at=timezone.now(),
        )
        self.reading = CloseoutReading.objects.create(
            work_order=self.work,
            step_execution=self.execution,
            label='Original check',
            phase='before',
            raw_text='0 bar',
            value=Decimal('0'),
            unit='bar',
            expected_min=Decimal('0'),
            expected_max=Decimal('1'),
            normalization_rule_version='fixture.v1',
            recorded_by=self.user,
        )

    def test_overview_carries_the_original_records_without_writes(self):
        """Repeated reads preserve identity, zero, false, bounds and lifecycle."""
        counts = (self.work.events.count(), WorkOrder.objects.count())
        for _ in range(2):
            response = self.get(
                reverse('kanban-card-overview', kwargs={'pk': self.work.pk}),
                expected_code=200,
            )
            value = response.data['routine_work']
            self.assertEqual(value['work_order_type'], 'calibration')
            task = value['applications'][0]['tasks'][0]
            self.assertEqual(task['value'], {'boolean': False})
            self.assertEqual(task['step']['title'], 'Original instruction')
            reading = value['readings'][0]
            self.assertEqual(reading['id'], self.reading.pk)
            self.assertEqual(Decimal(reading['value']), 0)
            self.assertEqual(Decimal(reading['expected_min']), 0)
            self.assertEqual(reading['phase'], 'before')
            self.assertEqual(reading['step_execution'], self.execution.pk)
            self.assertNotIn('as_found', reading)
            self.assertNotIn('instrument', reading)
        self.work.refresh_from_db()
        self.assertEqual(self.work.lifecycle_version, 1)
        self.assertEqual(counts, (self.work.events.count(), WorkOrder.objects.count()))

    def test_types_share_records_without_fabricating_different_tasks(self):
        """The recorded type selects presentation; it does not synthesize content."""
        for work_type in ('preventive', 'inspection', 'calibration'):
            self.work.work_order_type = work_type
            result = project(self.work)
            self.assertEqual(result['work_order_type'], work_type)
            self.assertEqual(
                result['applications'][0]['tasks'][0]['id'], self.execution.pk
            )
        for work_type in ('corrective', 'other'):
            self.work.work_order_type = work_type
            self.assertIsNone(project(self.work))

    def test_sibling_records_are_never_borrowed(self):
        """An empty routine Work cannot inherit another Work's results."""
        sibling = WorkOrder.objects.create(
            title='Sibling', work_order_type='inspection'
        )
        result = project(sibling)
        self.assertEqual(result['applications'], [])
        self.assertEqual(result['readings'], [])

    def test_not_applicable_does_not_reattribute_prior_values(self):
        """Retained old values are not current measurements by a disposition actor."""
        self.execution.status = 'not_applicable'
        self.execution.note = 'Earlier note'
        self.execution.disposition_reason = 'Not applicable at this location'
        self.execution.save()
        result = project(self.work)['applications'][0]['tasks'][0]
        self.assertIsNone(result['value'])
        self.assertEqual(result['note'], '')
        self.assertEqual(
            result['disposition_reason'], self.execution.disposition_reason
        )

    @override_settings(
        AIMMS_PROCEDURES_ENABLED=False, AIMMS_CLOSEOUT_WIZARD_ENABLED=False
    )
    def test_disabled_sources_are_unavailable(self):
        """Flags withhold records instead of reporting an evaluated empty success."""
        result = project(self.work)
        self.assertEqual(result['procedures_state'], 'not_enabled')
        self.assertEqual(result['readings_state'], 'not_enabled')
        self.assertEqual(result['applications'], [])
        self.assertEqual(result['readings'], [])

    def test_execution_snapshot_does_not_follow_catalogue_edits(self):
        """The reader does not replace the original applied instruction."""
        self.application.snapshot = {'steps': [{'title': 'Later differing definition'}]}
        self.application.save(update_fields=['snapshot'])
        result = project(self.work)
        self.assertEqual(
            result['applications'][0]['tasks'][0]['step']['title'],
            'Original instruction',
        )

    def test_foreign_parent_is_unavailable(self):
        """The existing overview scope authorizes the entire new projection."""
        from company.models import Company

        self.work.customer = Company.objects.create(
            name='Foreign customer', is_customer=True
        )
        self.work.save(update_fields=['customer'])
        self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work.pk}),
            expected_code=404,
        )
