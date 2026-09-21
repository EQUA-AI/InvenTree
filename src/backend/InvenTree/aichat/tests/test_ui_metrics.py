"""Operational widget counts reconcile with complete authorized populations."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from tasks.models import WorkOrder, WorkOrderLifecycle, WorkOrderType

from company.models import Company
from InvenTree.helpers import current_date
from order.models import PurchaseOrder, PurchaseOrderLineItem


class ManagementMetricsTests(TestCase):
    """Counts, date fallback, missing data and live role revocation."""

    def setUp(self):
        """Use disposable records and an explicit scope resolver seam."""
        self.actor = get_user_model().objects.create_superuser(
            username='ui-metrics', password='test'
        )
        self.client.force_login(self.actor)
        self.today = current_date()
        self.url = '/api/aichat/ui/metrics/'

    def test_orders_are_distinct_and_header_fallback_is_explicit(self):
        """Two late lines count one order; fulfilled lines never count."""
        supplier = Company.objects.create(name='Metrics supplier', is_supplier=True)
        order = PurchaseOrder.objects.create(
            supplier=supplier,
            reference='UI-1',
            status=10,
            target_date=self.today - timedelta(days=1),
        )
        for _ in range(2):
            PurchaseOrderLineItem.objects.create(order=order, quantity=3, received=1)
        response = self.client.get(self.url, {'metric': 'overdue-purchase'})
        self.assertEqual(response.status_code, 200)
        metric = next(
            row for row in response.json()['metrics'] if row['id'] == 'overdue-purchase'
        )
        self.assertEqual(metric['count'], 1)
        self.assertEqual(metric['records'][0]['pk'], order.pk)
        self.assertEqual(
            metric['records'][0]['due_date'], order.target_date.isoformat()
        )
        order.lines.update(received=3)
        self.assertEqual(
            next(
                row
                for row in self.client.get(self.url).json()['metrics']
                if row['id'] == 'overdue-purchase'
            )['count'],
            0,
        )

    def test_maintenance_counts_full_population_and_excludes_today_and_closed(self):
        """The 25-record page is not the aggregate, and missing dates stay visible."""
        from django.db.models import Q

        for i in range(28):
            WorkOrder.objects.create(
                title=f'Fixture {i}',
                status='backlog',
                priority='low',
                work_order_type=WorkOrderType.PREVENTIVE,
                lifecycle_status=WorkOrderLifecycle.PLANNED,
                due_date=self.today - timedelta(days=1),
            )
        WorkOrder.objects.create(
            title='Missing date',
            status='backlog',
            priority='low',
            work_order_type=WorkOrderType.PREVENTIVE,
            lifecycle_status=WorkOrderLifecycle.PLANNED,
        )
        WorkOrder.objects.create(
            title='Due today',
            status='backlog',
            priority='low',
            work_order_type=WorkOrderType.PREVENTIVE,
            lifecycle_status=WorkOrderLifecycle.PLANNED,
            due_date=self.today,
        )
        with mock.patch(
            'tasks.scope.work_order_scope_filter',
            return_value=Q(title__startswith='Fixture')
            | Q(title='Missing date')
            | Q(title='Due today'),
        ):
            response = self.client.get(self.url, {'metric': 'maintenance-due'})
            metric = next(
                row
                for row in response.json()['metrics']
                if row['id'] == 'maintenance-due'
            )
            self.assertEqual(metric['count'], 28)
            self.assertEqual(len(metric['records']), 25)
            self.assertEqual(metric['missing_dates'], 1)
            self.assertEqual(metric['next_offset'], 25)
        with mock.patch(
            'tasks.scope.work_order_scope_filter', return_value=Q(pk__in=[])
        ):
            metric = next(
                row
                for row in self.client.get(self.url).json()['metrics']
                if row['id'] == 'maintenance-due'
            )
            self.assertEqual(metric['count'], 0)

    def test_role_revocation_and_anonymous_access(self):
        """Old sessions do not keep counts after their access is removed."""
        self.actor.is_superuser = False
        self.actor.save(update_fields=['is_superuser'])
        self.assertEqual(self.client.get(self.url).json()['metrics'], [])
        self.client.logout()
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
