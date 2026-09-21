"""Widget populations, time boundaries, projections, and authorization."""

import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from assets.models import AssetMachine, Client, ClientScopeGrant
from tasks.dashboard_metrics import METRICS, MetricOptions, dashboard_metric
from tasks.models import WorkOrder, WorkOrderPart
from tasks.scope import ScopeError


def widget_scopes(actor):
    """Resolve actual test grants, including their removal between requests."""
    return [
        {'client_id': pk}
        for pk in ClientScopeGrant.objects.filter(user=actor).values_list(
            'client_id', flat=True
        )
    ]


@override_settings(
    USE_TZ=True,
    AIMMS_PLANT_TIMEZONE='UTC',
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=widget_scopes,
)
class MaintenanceDashboardTests(TestCase):
    """Exercise real ORM projections without changing any live data."""

    def setUp(self):
        """Create two isolated clients with only one granted to the reader."""
        self.actor = get_user_model().objects.create_superuser(
            username='widget-reader', password='test'
        )
        self.tenant = Client.objects.create(code='widgets-test', name='Widget plant')
        self.other = Client.objects.create(code='widgets-other', name='Other plant')
        ClientScopeGrant.objects.create(user=self.actor, client=self.tenant)
        self.machine = AssetMachine.objects.create(
            name='Widget machine', client=self.tenant
        )
        self.hidden = AssetMachine.objects.create(
            name='Hidden widget machine', client=self.other
        )
        self.now = datetime(2026, 9, 21, 12, tzinfo=dt_timezone.utc)
        self.client.force_login(self.actor)
        self.url = '/api/aichat/ui/maintenance-metrics/'

    def order(self, **kwargs):
        """Create a work order and its normal generated card."""
        values = {
            'title': 'Widget job',
            'status': 'backlog',
            'priority': 'low',
            'machine': self.machine,
            'lifecycle_status': 'planned',
        }
        values.update(kwargs)
        return WorkOrder.objects.create(**values)

    def metric(self, name, **filters):
        """Call the same service as the HTTP adapter with a fixed clock."""
        return dashboard_metric(
            self.actor, MetricOptions({'metric': name, **filters}, now=self.now)
        )

    def test_all_fifteen_have_honest_empty_results(self):
        """No-data durations/rates stay null; valid counts are zero."""
        self.assertEqual(len(METRICS), 15)
        for metric in METRICS:
            with self.subTest(metric=metric):
                result = self.metric(metric)
                self.assertTrue(result['complete'])
                if metric in {'elapsed', 'pm-on-time', 'downtime'}:
                    self.assertIsNone(result['value'])
                elif metric == 'data-health':
                    self.assertEqual(result['value'], 1)
                else:
                    self.assertEqual(result['value'], 0)

    def test_open_is_distinct_complete_and_not_creation_filtered(self):
        """More than a page, drafts, cards, terminal states and other tenants."""
        for _ in range(28):
            self.order()
        self.order(lifecycle_status='draft')
        self.order(lifecycle_status='completed')
        self.order(lifecycle_status='canceled')
        self.order(is_active=False)
        self.order(machine=self.hidden)
        result = self.metric(
            'open', period='custom', **{'from': '2000-01-01', 'to': '2000-01-02'}
        )
        self.assertEqual(result['value'], 28)
        self.assertEqual(result['stats']['drafts'], 1)
        self.assertEqual(len(result['records']), 25)
        self.assertEqual(result['next_offset'], 25)
        page = self.metric('open', offset='25')
        self.assertEqual(len(page['records']), 3)
        self.assertIsNone(page['next_offset'])
        self.assertTrue(all(r['model'] == 'workorder' for r in page['records']))

    def test_completed_uses_actual_clock_and_keeps_archive(self):
        """Creation/last-edit time cannot manufacture a completion."""
        completed = self.order(
            lifecycle_status='completed',
            actual_completed_at=self.now - timedelta(days=1),
            is_active=False,
        )
        WorkOrder.objects.filter(pk=completed.pk).update(
            created_at=self.now - timedelta(days=90)
        )
        self.order(lifecycle_status='completed')
        self.order(
            lifecycle_status='completed',
            actual_completed_at=self.now - timedelta(days=35),
        )
        self.order(lifecycle_status='canceled', actual_completed_at=self.now)
        result = self.metric('completed')
        self.assertEqual(result['value'], 1)
        self.assertEqual(result['records'][0]['pk'], completed.pk)
        self.assertEqual(result['missing']['completion_date_unknown_period'], 1)
        self.assertEqual(sum(g['count'] for g in result['groups']), 1)

    def test_pm_buckets_and_overdue_are_disjoint(self):
        """Today is never overdue; future window end is inclusive."""
        for delta in (-1, 0, 7, 8):
            self.order(
                work_order_type='preventive',
                due_date=self.now.date() + timedelta(days=delta),
            )
        self.order(work_order_type='preventive')
        self.order(due_date=self.now.date() - timedelta(days=2))
        result = self.metric('preventive')
        self.assertEqual(result['value'], 3)
        self.assertEqual(result['missing']['due_date'], 1)
        self.assertEqual(
            {g['key']: g['count'] for g in result['groups']},
            {'overdue': 1, 'today': 1, 'upcoming': 1},
        )
        self.assertEqual(self.metric('overdue')['value'], 2)
        self.assertEqual(self.metric('preventive', group='today')['detail_count'], 1)

    def test_assignment_age_and_filters(self):
        """Identity assignments, canonical type and criticality filters."""
        order = self.order(
            assigned_to=self.actor, priority='high', estimated_minutes=60
        )
        WorkOrder.objects.filter(pk=order.pk).update(
            created_at=self.now - timedelta(days=40)
        )
        self.order(assignee='Legacy name')
        self.assertEqual(self.metric('assignment')['stats']['unassigned'], 1)
        self.assertEqual(self.metric('assignment')['stats']['legacy_assignment'], 1)
        self.assertEqual(self.metric('assignment', mine='true')['value'], 1)
        self.assertEqual(self.metric('age', group='31-60')['detail_count'], 1)
        self.assertEqual(self.metric('open', machine=str(self.hidden.pk))['value'], 0)
        self.assertEqual(self.metric('open', criticality='critical')['value'], 0)
        self.assertEqual(self.metric('open', priority='high')['value'], 1)

    def test_holds_count_union_once(self):
        """Two missing allocations on one held order never triple count it."""
        from part.models import Part

        held = self.order(
            lifecycle_status='on_hold', hold_reason='Waiting for approval'
        )
        for name in ['Widget part A', 'Widget part B']:
            part = Part.objects.create(name=name)
            WorkOrderPart.objects.create(
                work_order=held, part=part, quantity=2, allocated_quantity=1
            )
        result = self.metric('holds')
        self.assertEqual(result['value'], 1)
        self.assertEqual(result['stats']['overlap'], 1)
        self.assertEqual(len(result['groups']), 2)

    def test_verification_uses_latest_entry(self):
        """Missing history stays unknown; latest re-entry owns the clock."""
        from tasks.workorder_models import WorkOrderEvent

        order = self.order(lifecycle_status='verifying')
        self.order(lifecycle_status='verifying')
        for days in (4, 1):
            event = WorkOrderEvent.objects.create(
                work_order=order,
                event_type='TRANSITION',
                to_status='verifying',
                correlation_id=uuid.uuid4(),
            )
            WorkOrderEvent.objects.filter(pk=event.pk).update(
                created_at=self.now - timedelta(days=days)
            )
        result = self.metric('verification')
        self.assertEqual(result['missing']['verification_start'], 1)
        self.assertEqual(
            result['records'][0]['verifying_since'], self.now - timedelta(days=1)
        )

    def test_pm_rate_includes_unfinished_and_unknown(self):
        """Completed-only denominators would inflate this to 100 percent."""
        due = self.now.date() - timedelta(days=2)
        self.order(
            work_order_type='preventive',
            due_date=due,
            lifecycle_status='completed',
            actual_completed_at=self.now - timedelta(days=3),
        )
        self.order(work_order_type='preventive', due_date=due)
        self.order(
            work_order_type='preventive', due_date=due, lifecycle_status='completed'
        )
        self.order(
            work_order_type='preventive', due_date=due, lifecycle_status='canceled'
        )
        result = self.metric('pm-on-time')
        self.assertEqual(result['stats']['denominator'], 3)
        self.assertEqual(result['value'], 33.3)

    def test_duration_mix_and_repeated_corrective_work(self):
        """Elapsed time excludes missing/negative pairs; repeats count machines."""
        for minutes in (30, 90):
            self.order(
                lifecycle_status='completed',
                actual_started_at=self.now - timedelta(minutes=minutes + 1),
                actual_completed_at=self.now - timedelta(minutes=1),
            )
        self.order(
            lifecycle_status='completed',
            work_order_type='inspection',
            actual_completed_at=self.now - timedelta(days=1),
        )
        elapsed = self.metric('elapsed')
        self.assertEqual(elapsed['value'], 60)
        self.assertEqual(elapsed['missing']['actual_start'], 1)
        self.assertEqual(self.metric('mix')['record_count'], 3)
        repeat = self.metric('repeat')
        self.assertEqual(repeat['value'], 1)
        self.assertEqual(repeat['record_count'], 2)

    def test_effective_downtime_uses_amendment_and_keeps_zero(self):
        """Applied immutable corrections replace the original amount."""
        from tasks.closeout_models import CloseoutAmendment
        from tasks.workorder_models import WorkOrderCloseout

        for minutes in (60, 0, None):
            order = self.order(
                lifecycle_status='completed',
                actual_completed_at=self.now - timedelta(days=1),
            )
            closeout = WorkOrderCloseout.objects.create(
                work_order=order,
                action='Repair',
                result='Complete',
                verification_summary='Checked',
                downtime_minutes=minutes,
                completed_by=self.actor,
                completed_at=self.now - timedelta(days=1),
            )
            if minutes == 60:
                CloseoutAmendment.objects.create(
                    closeout=closeout,
                    changes={},
                    requested_by=self.actor,
                    reason='Correction',
                    status='applied',
                    applied_at=self.now,
                    effective_snapshot={'closeout': {'downtime_minutes': 20}},
                )
        result = self.metric('downtime')
        self.assertEqual(result['value'], 20)
        self.assertEqual(result['record_count'], 2)
        self.assertEqual(result['missing']['downtime'], 1)
        self.assertTrue(any(r.get('amended') for r in result['records']))

    def test_health_is_scoped_distinct_and_freshness_is_not_downtime(self):
        """Two alerts count one machine; missing observations remain stale."""
        from assets.health_models import (
            HealthSource,
            MachineAnomaly,
            MachineSignalBinding,
            MachineSignalState,
        )

        for index, severity in enumerate(['warning', 'critical']):
            MachineAnomaly.objects.create(
                machine=self.machine,
                severity=severity,
                fingerprint=f'widget-{index}',
                title='Alert',
                first_observed_at=self.now - timedelta(days=1),
                last_observed_at=self.now,
            )
        MachineAnomaly.objects.create(
            machine=self.hidden,
            severity='critical',
            fingerprint='hidden-widget',
            title='Hidden alert',
            first_observed_at=self.now,
            last_observed_at=self.now,
        )
        result = self.metric('alerts')
        self.assertEqual(result['value'], 1)
        self.assertEqual(result['records'][0]['alert_count'], 2)
        self.assertEqual(result['groups'][0]['key'], 'critical')
        source = HealthSource.objects.create(
            name='Widget source', source_type='manual', last_success_at=self.now
        )
        binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=source,
            external_key='widget-temp',
            display_name='Temperature',
        )
        self.assertEqual(self.metric('data-health')['groups'][0]['key'], 'stale')
        MachineSignalState.objects.create(
            binding=binding, observed_at=self.now, quality='good'
        )
        self.assertEqual(self.metric('data-health')['groups'][0]['key'], 'current')
        MachineSignalState.objects.filter(binding=binding).update(quality='uncertain')
        self.assertEqual(self.metric('data-health')['groups'][0]['key'], 'degraded')

    def test_timezone_boundaries_and_invalid_parameters(self):
        """Local-day conversion, half-open upper edge and bounded filters."""
        with override_settings(AIMMS_PLANT_TIMEZONE='America/New_York'):
            o = MetricOptions(
                {
                    'metric': 'completed',
                    'period': 'custom',
                    'from': '2026-03-08',
                    'to': '2026-03-08',
                },
                now=self.now,
            )
            self.assertEqual(
                (
                    o.end.astimezone(dt_timezone.utc)
                    - o.start.astimezone(dt_timezone.utc)
                ).total_seconds(),
                23 * 3600,
            )
            self.assertTrue(o.in_period(o.start))
            self.assertFalse(o.in_period(o.end))
        for params in (
            {'metric': 'created-vs-completed'},
            {'offset': '-1'},
            {'priority': 'urgent'},
            {'metric': 'alerts', 'mine': 'true'},
            {'period': 'custom', 'from': 'bad', 'to': 'bad'},
        ):
            self.assertEqual(self.client.get(self.url, params).status_code, 400)

    def test_endpoint_role_scope_revocation_and_unavailable(self):
        """Roles and grants are read afresh on each request."""
        self.order()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['value'], 1)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        ClientScopeGrant.objects.filter(user=self.actor).delete()
        self.assertEqual(self.client.get(self.url).json()['state'], 'unavailable')
        self.actor.is_superuser = False
        self.actor.save(update_fields=['is_superuser'])
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_scope_failure_does_not_claim_zero(self):
        """Unavailable data is distinct from a verified empty population."""
        with mock.patch(
            'tasks.dashboard_metrics.machine_scope_filter', side_effect=ScopeError
        ):
            result = self.client.get(self.url).json()
        self.assertFalse(result['complete'])
        self.assertNotIn('value', result)

    def test_work_order_access_does_not_disclose_ungranted_machine(self):
        """A customer-scoped job does not grant its asset's independent scope."""
        from django.db.models import Q

        order = self.order(machine=self.hidden)
        with mock.patch(
            'tasks.dashboard_metrics.work_order_scope_filter',
            return_value=Q(pk=order.pk),
        ):
            result = self.metric('open')
        self.assertEqual(result['value'], 1)
        self.assertIsNone(result['records'][0]['machine_label'])
        self.assertIsNone(result['records'][0]['visible_machine'])
        self.assertNotIn(str(self.hidden.pk), [m['value'] for m in result['machines']])

    def test_health_query_count_does_not_grow_per_machine(self):
        """The fleet view batches signal reads instead of N HTTP/ORM requests."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for index in range(35):
            AssetMachine.objects.create(name=f'Fleet {index}', client=self.tenant)
        with CaptureQueriesContext(connection) as captured:
            result = self.metric('data-health')
        self.assertEqual(result['value'], 36)
        self.assertLessEqual(len(captured), 8)
