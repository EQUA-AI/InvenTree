"""API tests for the machine Maintenance blade projection.

The blade is the durable job-history index for a machine. These tests pin the
read contract it depends on: every linked row carries enough of the work order
to render an authoritative link, unlinked legacy rows stay readable without a
fabricated one, the list does not issue a query per row, and a caller who may
read the history but not the work order never receives its id.
"""

from django.test import override_settings
from django.utils import timezone

from tasks.closeout_models import CloseoutAmendment, CloseoutAmendmentStatus
from tasks.models import WorkOrder, WorkOrderCloseout, WorkOrderLifecycle, WorkOrderType
from tasks.scope import MaintenanceScope

from assets.models import AssetMachine, AssetMaintenanceRecord
from company.models import Company
from InvenTree.unit_test import InvenTreeAPITestCase

MAINTENANCE_URL = '/api/assets/maintenance/'

# The request user is reloaded per request, so scope is supplied through the
# deployment resolver hook rather than an attribute on the in-memory actor.
_GRANTED_CUSTOMER_IDS: list[int] = []


def _scope_resolver(actor):
    """Return the customer scopes granted to the current test actor."""
    return {
        MaintenanceScope(customer_id=customer_id, site_key=None)
        for customer_id in _GRANTED_CUSTOMER_IDS
    }


class MaintenanceRecordProjectionTest(InvenTreeAPITestCase):
    """The maintenance list projects the linked work order safely."""

    roles = 'all'

    def setUp(self):
        """Create a machine with one linked and one unlinked history row."""
        super().setUp()

        self.customer = Company.objects.create(name='Northgate Water', is_customer=True)
        self.machine = AssetMachine.objects.create(name='Influent Pump Station No. 9')
        self.completed_at = timezone.now()

        self.work_order = WorkOrder.objects.create(
            title='Influent Pump Station No. 9: seal and wear-ring repair',
            reference='WO-TEST-000001',
            status=WorkOrder.STATUS_DONE,
            priority=WorkOrder.PRIORITY_HIGH,
            machine=self.machine,
            customer=self.customer,
            work_order_type=WorkOrderType.CORRECTIVE,
            lifecycle_status=WorkOrderLifecycle.COMPLETED,
            actual_completed_at=self.completed_at,
            is_active=False,
        )
        WorkOrderCloseout.objects.create(
            work_order=self.work_order,
            action='Replaced mechanical seal and wear ring',
            result='Vibration returned to baseline',
            verification_summary='Two-hour stable run verified',
            downtime_minutes=432,
            follow_up_required=True,
            completed_by=self.user,
            completed_at=self.completed_at,
            verified_by=self.user,
            verified_at=self.completed_at,
            content_hash='0' * 64,
        )

        self.linked = AssetMaintenanceRecord.objects.create(
            machine=self.machine,
            date=self.completed_at.date(),
            summary='Pump 2 seal and wear-ring repair',
            details='Replaced the mechanical seal and wear ring.',
            performed_by='R. Shuruncle',
            work_order=self.work_order,
        )
        self.legacy = AssetMaintenanceRecord.objects.create(
            machine=self.machine,
            date=self.completed_at.date(),
            summary='Legacy paper record',
            details='Imported from the old CMMS with no work order.',
            performed_by='Unknown',
        )

        self.url = MAINTENANCE_URL
        _GRANTED_CUSTOMER_IDS.clear()
        self.addCleanup(_GRANTED_CUSTOMER_IDS.clear)

    def _rows(self):
        response = self.get(self.url, {'machine': self.machine.pk}, expected_code=200)
        results = response.data
        if isinstance(results, dict):
            results = results['results']
        return {row['summary']: row for row in results}

    def test_linked_row_projects_the_full_work_order_summary(self):
        """A linked row carries reference, type, lifecycle and closeout facts."""
        row = self._rows()['Pump 2 seal and wear-ring repair']

        self.assertEqual(row['work_order'], self.work_order.pk)
        self.assertEqual(row['work_order_reference'], 'WO-TEST-000001')
        self.assertEqual(row['work_order_title'], self.work_order.title)
        self.assertEqual(row['work_order_type'], WorkOrderType.CORRECTIVE)
        self.assertEqual(row['lifecycle_status'], WorkOrderLifecycle.COMPLETED)
        self.assertIsNotNone(row['actual_completed_at'])
        self.assertEqual(row['downtime_minutes'], 432)
        self.assertTrue(row['verified'])
        self.assertTrue(row['follow_up_required'])

    def test_unlinked_legacy_row_offers_no_fabricated_link(self):
        """An unowned legacy row stays readable with every link field null."""
        row = self._rows()['Legacy paper record']

        self.assertIsNone(row['work_order'])
        self.assertIsNone(row['work_order_reference'])
        self.assertIsNone(row['work_order_title'])
        self.assertIsNone(row['work_order_type'])
        self.assertIsNone(row['lifecycle_status'])
        self.assertIsNone(row['actual_completed_at'])
        self.assertIsNone(row['downtime_minutes'])
        self.assertFalse(row['verified'])
        self.assertFalse(row['follow_up_required'])

    def test_applied_amendment_projects_effective_values(self):
        """The blade shows amended closeout facts, marked as amended."""
        closeout = self.work_order.structured_closeout
        CloseoutAmendment.objects.create(
            closeout=closeout,
            changes={
                'downtime_minutes': {'from': 432, 'to': 318},
                'follow_up_required': {'from': True, 'to': False},
            },
            base_content_hash=closeout.content_hash,
            reason='Downtime double-counted the stable run',
            requested_by=self.user,
            status=CloseoutAmendmentStatus.APPLIED,
            effective_snapshot={
                'closeout': {'downtime_minutes': 318, 'follow_up_required': False}
            },
            effective_snapshot_hash='1' * 64,
            decided_by=self.user,
            applied_at=self.completed_at,
        )

        row = self._rows()['Pump 2 seal and wear-ring repair']

        self.assertEqual(row['downtime_minutes'], 318)
        self.assertFalse(row['follow_up_required'])
        self.assertTrue(row['amended'])

        legacy = self._rows()['Legacy paper record']
        self.assertFalse(legacy['amended'])

    def test_projection_does_not_scale_queries_with_rows(self):
        """Adding history rows must not add a query per row."""

        def fetch():
            self.get(self.url, {'machine': self.machine.pk}, expected_code=200)

        # 16: the closeout-amendment prefetch adds exactly one query to the
        # request, independent of row count — the flat-count property below
        # is what this test protects.
        with self.assertNumQueriesLessThan(16):
            fetch()

        for index in range(8):
            work_order = WorkOrder.objects.create(
                title=f'Routine job {index}',
                reference=f'WO-TEST-90000{index}',
                status=WorkOrder.STATUS_DONE,
                priority=WorkOrder.PRIORITY_LOW,
                machine=self.machine,
                customer=self.customer,
                lifecycle_status=WorkOrderLifecycle.COMPLETED,
                is_active=False,
            )
            AssetMaintenanceRecord.objects.create(
                machine=self.machine,
                date=self.completed_at.date(),
                summary=f'Routine job {index}',
                performed_by='Route crew',
                work_order=work_order,
            )

        with self.assertNumQueriesLessThan(16):
            fetch()

    @override_settings(
        AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_MAINTENANCE_SCOPE_RESOLVER=_scope_resolver
    )
    def test_out_of_scope_work_order_id_is_withheld(self):
        """A caller outside the work order's scope gets no link and no id."""
        _GRANTED_CUSTOMER_IDS[:] = [
            Company.objects.create(name='Other utility', is_customer=True).pk
        ]

        row = self._rows()['Pump 2 seal and wear-ring repair']

        # The row itself is still readable - only the link is withheld.
        self.assertEqual(row['summary'], 'Pump 2 seal and wear-ring repair')
        self.assertIsNone(row['work_order'])
        self.assertIsNone(row['work_order_reference'])
        self.assertIsNone(row['work_order_title'])
        self.assertIsNone(row['downtime_minutes'])

    @override_settings(
        AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_MAINTENANCE_SCOPE_RESOLVER=_scope_resolver
    )
    def test_in_scope_work_order_link_is_exposed(self):
        """An actor holding the work order's customer scope keeps the link."""
        _GRANTED_CUSTOMER_IDS[:] = [self.customer.pk]

        row = self._rows()['Pump 2 seal and wear-ring repair']

        self.assertEqual(row['work_order'], self.work_order.pk)
        self.assertEqual(row['work_order_reference'], 'WO-TEST-000001')
