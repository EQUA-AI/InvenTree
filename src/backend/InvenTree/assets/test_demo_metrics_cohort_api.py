"""Cohort, coverage, history and scoped API tests (work packages D/G).

Includes the regression tests for the previously unscoped ``_machine`` parent
lookup in the machine-health API.
"""

import datetime as dt
import json
from itertools import pairwise

from django.test import TestCase

from tasks.scope import MaintenanceScope

from aimms_testing import requires_postgres
from assets.demo_metrics import cleanup, cohort, contract, history, reference, replay
from assets.demo_metrics.demo_test_support import DemoMetricsEnvMixin
from assets.demo_metrics_models import (
    DemoMetricsCoverageInterval,
    DemoMetricsDowntimeInterval,
    DemoMetricsSession,
)


def translated_instant(fixture, session, value):
    """One fixture instant translated to the session anchor timeline."""
    return reference.aware_utc(
        contract.translate_instant(
            reference.timestamp(value),
            fixture.as_of,
            reference.aware_utc(session.anchor_at),
        )
    )


def translated_window(fixture, session, field):
    """Fixture history bound translated to the session anchor timeline."""
    return translated_instant(fixture, session, fixture.data['history'][field])


@requires_postgres
class CohortAndCoverageTest(DemoMetricsEnvMixin, TestCase):
    """The authorized cohort and per-machine coverage projection."""

    def setUp(self):
        """Apply a fresh synthetic session."""
        self.build_demo_env()
        self.result = self.apply_demo()
        self.session = self.session()

    def test_cohort_is_the_authorized_intersection(self):
        """Exclude unauthorized actors from session membership."""
        members = cohort.session_cohort(self.session, self.actor)
        self.assertEqual({m.alias for m in members}, set(self.machines))
        # An actor whose scope excludes every member is denied, not zeroed.
        outsider = self.make_outsider()
        with self.assertRaises(cohort.CohortError) as caught:
            cohort.session_cohort(self.session, outsider)
        self.assertEqual(caught.exception.code, 'SCOPE_DENIED')

    def test_location_filter_and_descendants(self):
        """Distinguish direct placement from descendant membership."""
        site_a = self.locations['SITE-A']
        members = cohort.session_cohort(
            self.session, self.actor, location_id=site_a.pk, descendants=True
        )
        self.assertEqual({m.alias for m in members}, {'A01', 'A02', 'A03'})
        members = cohort.session_cohort(
            self.session, self.actor, location_id=site_a.pk, descendants=False
        )
        self.assertEqual({m.alias for m in members}, {'A01'})

    def test_machine_coverage_scenarios(self):
        """Keep freshness and condition distinct across the fixture."""
        coverage = {
            m.alias: cohort.machine_coverage(m)
            for m in cohort.session_cohort(self.session, self.actor)
        }
        self.assertEqual(
            (coverage['A01'].coverage_state, coverage['A01'].condition),
            ('fresh', 'normal'),
        )
        self.assertEqual(
            (coverage['A02'].coverage_state, coverage['A02'].condition),
            ('fresh', 'warning'),
        )
        self.assertEqual(
            (coverage['A03'].coverage_state, coverage['A03'].condition), ('stale', None)
        )
        self.assertEqual(
            (coverage['B01'].coverage_state, coverage['B01'].condition),
            ('fresh', 'critical'),
        )
        self.assertEqual(coverage['B02'].coverage_state, 'never_seen')
        self.assertEqual(coverage['B03'].coverage_state, 'not_configured')

    def test_current_metrics_counts(self):
        """Reconcile current metrics with the reference cohort."""
        metrics = cohort.session_current_metrics(self.session, self.actor)
        self.assertEqual(metrics['cohort_size'], 6)
        self.assertEqual(metrics['observation_coverage']['fresh'], 3)
        self.assertEqual(metrics['observation_coverage']['stale'], 1)
        self.assertEqual(metrics['observation_coverage']['never_seen'], 1)
        self.assertEqual(metrics['observation_coverage']['not_configured'], 1)
        self.assertEqual(
            metrics['fresh_condition'], {'normal': 1, 'warning': 1, 'critical': 1}
        )
        self.assertEqual(metrics['open_work_orders'], 7)
        self.assertEqual(metrics['machines_with_open_work'], 5)
        self.assertEqual(metrics['overdue_open_work_orders'], 3)
        self.assertEqual(
            metrics['open_by_state'],
            {'in_progress': 1, 'on_hold': 1, 'planned': 2, 'ready': 2, 'verifying': 1},
        )

    def test_order_membership_requires_session_membership(self):
        """Exclude unrelated orders even on session machines."""
        from tasks.models import WorkOrder, WorkOrderLifecycle

        WorkOrder.objects.create(
            title='operator job on A01',
            status='ready',
            priority='high',
            machine_id=self.machines['A01'].pk,
            lifecycle_status=WorkOrderLifecycle.PLANNED,
        )
        metrics = cohort.session_current_metrics(self.session, self.actor)
        self.assertEqual(metrics['open_work_orders'], 7)

    def make_outsider(self):
        """Create an actor scoped to a different client."""
        from django.contrib.auth import get_user_model

        outsider = get_user_model().objects.create_user(
            username='outsider', email='o@example.com', password='pw'
        )
        outsider.maintenance_scopes = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_tenant.pk + 1
            )
        }
        return outsider


@requires_postgres
class HistoryImportTest(DemoMetricsEnvMixin, TestCase):
    """Durable interval rows with explicit synthetic attribution."""

    def setUp(self):
        """Apply a session with approved historical intervals."""
        self.build_demo_env(history_import_approved=True)
        self.result = self.apply_demo(include_history=True)
        self.session = self.session()

    def test_history_rows_are_durable_and_attributed(self):
        """Persist interval inventory and synthetic attribution."""
        self.assertEqual(DemoMetricsCoverageInterval.objects.count(), 28)
        self.assertEqual(DemoMetricsDowntimeInterval.objects.count(), 13)
        row = DemoMetricsCoverageInterval.objects.first()
        self.assertEqual(row.attribution_mode, history.ATTRIBUTION_MODE)
        self.assertEqual(row.session_id, self.session.pk)
        self.assertTrue(row.planned)
        self.assertTrue(row.fully_observed)

    def test_reference_aggregation_numbers(self):
        """Match planned and lost machine-minutes to the reference."""
        window = history.aggregate_history(
            session=self.session,
            start=translated_window(self.fixture, self.session, 'start_at'),
            end=translated_window(self.fixture, self.session, 'end_at'),
        )
        self.assertAlmostEqual(window['planned_machine_minutes'], 13440)
        self.assertAlmostEqual(window['downtime_machine_minutes'], 410)
        self.assertAlmostEqual(
            window['measured_cohort_availability'], (13440 - 410) / 13440
        )
        self.assertEqual(window['measured_machines'], 2)

    def test_window_without_coverage_reports_nulls_not_zero(self):
        """Represent missing measurement as unknown rather than no loss."""
        later = translated_window(self.fixture, self.session, 'end_at') + dt.timedelta(
            days=5
        )
        window = history.aggregate_history(
            session=self.session, start=later, end=later + dt.timedelta(days=1)
        )
        self.assertEqual(window['planned_machine_minutes'], 0)
        self.assertIsNone(window['downtime_machine_minutes'])
        self.assertIsNone(window['measured_cohort_availability'])

    def test_daily_buckets_aggregate_the_union(self):
        """Reconcile daily loss buckets with the total union."""
        window = history.aggregate_history(
            session=self.session,
            start=translated_window(self.fixture, self.session, 'start_at'),
            end=translated_window(self.fixture, self.session, 'end_at'),
        )
        totals = sum(bucket['planned_machine_minutes'] for bucket in window['daily'])
        self.assertEqual(totals, 13440)
        for bucket in window['daily']:
            if bucket['planned_machine_minutes']:
                self.assertIsNotNone(bucket['downtime_machine_minutes'])

    def test_overlapping_coverage_is_rejected_at_import(self):
        """Refuse overlapping planned coverage before importing it."""
        data = json.loads(json.dumps(self.fixture.data))
        first = data['history']['coverage_intervals'][0]
        data['history']['coverage_intervals'].append({**first, 'key': 'OVERLAP'})
        fixture = contract.Fixture(
            data=data,
            canonical_sha256=self.fixture.canonical_sha256,
            file_sha256=self.fixture.file_sha256,
            as_of=self.fixture.as_of,
        )
        DemoMetricsCoverageInterval.objects.filter(session=self.session).delete()
        with self.assertRaises(history.HistoryError) as caught:
            history.import_history(
                fixture=fixture, mapping=self.mapping, session=self.session
            )
        self.assertEqual(caught.exception.code, 'OVERLAPPING_COVERAGE')


@requires_postgres
class DemoMetricsApiTest(DemoMetricsEnvMixin, TestCase):
    """Scoped read APIs: private, bounded, denied-not-zero."""

    def setUp(self):
        """Prepare an authorized current and historical API fixture."""
        self.build_demo_env(history_import_approved=True)
        self.result = self.apply_demo(include_history=True)
        self.session = self.session()
        self.start = translated_window(self.fixture, self.session, 'start_at')
        self.end = translated_window(self.fixture, self.session, 'end_at')

    def get(self, url, user=None):
        """Read an endpoint as the requested actor."""
        from rest_framework.test import APIClient

        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client.get(url)

    def test_sessions_list_is_bounded_and_scoped(self):
        """Only list sessions visible through authorized membership."""
        response = self.get('/api/assets/demo-metrics/sessions/', user=self.actor)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(
            response.data['results'][0]['session_key'], self.session.session_key
        )
        outsider = cohort_demo_outsider(self.client_tenant)
        denied = self.get('/api/assets/demo-metrics/sessions/', user=outsider)
        self.assertEqual(denied.status_code, 200)
        self.assertEqual(denied.data['count'], 0)

    def test_sessions_list_fails_closed_when_scope_unresolvable(self):
        """An unresolvable scope is an explicit denial, never the full list."""
        unscoped = cohort_demo_unscoped_user()
        response = self.get('/api/assets/demo-metrics/sessions/', user=unscoped)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['error'], 'SCOPE_DENIED')
        self.assertNotIn('results', response.data)

    def test_metrics_endpoint_reports_the_cohort(self):
        """Return scoped counts and cache-safe response metadata."""
        response = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/metrics/',
            user=self.actor,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['cohort_size'], 6)
        self.assertEqual(response.data['open_work_orders'], 7)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(len(response.data['machines']), 6)

    def test_metrics_location_filter(self):
        """Restrict metrics to the requested authorized location."""
        response = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/metrics/'
            f'?location={self.locations["SITE-A"].pk}',
            user=self.actor,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['cohort_size'], 3)

    def test_outsider_is_denied_not_zeroed(self):
        """Return denial rather than empty-looking metrics."""
        outsider = cohort_demo_outsider(self.client_tenant)
        response = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/metrics/',
            user=outsider,
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn('error', response.data)

    def test_history_endpoint_returns_reference_numbers(self):
        """Expose the imported reference interval totals."""
        response = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/history/'
            f'?start={reference.iso(self.start)}&end={reference.iso(self.end)}',
            user=self.actor,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['available'])
        self.assertAlmostEqual(response.data['planned_machine_minutes'], 13440)
        self.assertAlmostEqual(response.data['downtime_machine_minutes'], 410)

    def test_history_endpoint_is_unavailable_without_import(self):
        """Report absent history as unavailable."""
        DemoMetricsCoverageInterval.objects.filter(session=self.session).delete()
        response = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/history/'
            f'?start={reference.iso(self.start)}&end={reference.iso(self.end)}',
            user=self.actor,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['available'])
        self.assertNotEqual(response.data['reason'], '')

    def test_history_endpoint_requires_a_window(self):
        """Reject a history request without bounded dates."""
        response = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/history/',
            user=self.actor,
        )
        self.assertEqual(response.status_code, 400)

    def test_history_endpoint_applies_location_and_descendants(self):
        """History aggregates the same intersection the current metrics show."""
        base = (
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/history/'
            f'?start={reference.iso(self.start)}&end={reference.iso(self.end)}'
        )
        metrics = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/metrics/'
            f'?location={self.locations["SITE-A"].pk}',
            user=self.actor,
        )
        scoped = self.get(
            f'{base}&location={self.locations["SITE-A"].pk}', user=self.actor
        )
        self.assertEqual(scoped.status_code, 200)
        self.assertEqual(scoped.data['selected_machines'], 3)
        self.assertEqual(scoped.data['selected_machines'], metrics.data['cohort_size'])
        self.assertEqual(
            scoped.data['filters']['location_id'], self.locations['SITE-A'].pk
        )
        # Only A02 contributes coverage under SITE-A: half the full window.
        self.assertAlmostEqual(scoped.data['planned_machine_minutes'], 6720)
        direct = self.get(
            f'{base}&location={self.locations["SITE-A"].pk}&include_descendants=false',
            user=self.actor,
        )
        self.assertEqual(direct.status_code, 200)
        self.assertEqual(direct.data['selected_machines'], 1)
        self.assertEqual(direct.data['planned_machine_minutes'], 0)
        self.assertIsNone(direct.data['downtime_machine_minutes'])

    def test_history_endpoint_denies_outsider_not_zeroed(self):
        """Deny unauthorized history access without zeroed totals."""
        outsider = cohort_demo_outsider(self.client_tenant)
        response = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/history/'
            f'?start={reference.iso(self.start)}&end={reference.iso(self.end)}',
            user=outsider,
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn('error', response.data)

    def test_work_orders_list_reconciles_with_counts(self):
        """The contributing list and the metric counts cannot disagree."""
        metrics = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/metrics/',
            user=self.actor,
        )
        work = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/work-orders/',
            user=self.actor,
        )
        self.assertEqual(work.status_code, 200)
        self.assertEqual(work.data['count'], 9)
        self.assertEqual(work.data['open_count'], metrics.data['open_work_orders'])
        self.assertEqual(work.data['open_count'], 7)
        self.assertEqual(
            sum(1 for row in work.data['results'] if row['overdue']),
            metrics.data['overdue_open_work_orders'],
        )

    def test_bounded_work_list_keeps_full_cohort_counts(self):
        """Truncating contributing rows must not truncate aggregate counts."""
        from unittest.mock import patch

        metrics = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/metrics/',
            user=self.actor,
        )
        with patch('assets.demo_metrics_api.MAX_WORK_ROWS', 2):
            work = self.get(
                f'/api/assets/demo-metrics/sessions/{self.session.pk}/work-orders/',
                user=self.actor,
            )
        self.assertEqual(work.status_code, 200)
        self.assertEqual(len(work.data['results']), 2)
        self.assertEqual(work.data['count'], 9)
        self.assertEqual(work.data['open_count'], metrics.data['open_work_orders'])
        self.assertEqual(work.data['results_returned'], 2)
        self.assertTrue(work.data['has_more'])

    def test_work_orders_list_respects_location_filter(self):
        """Use the same location intersection for counts and rows."""
        response = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/work-orders/'
            f'?location={self.locations["SITE-A"].pk}',
            user=self.actor,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 5)
        metrics = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/metrics/'
            f'?location={self.locations["SITE-A"].pk}',
            user=self.actor,
        )
        self.assertEqual(response.data['open_count'], metrics.data['open_work_orders'])

    def test_work_orders_list_denies_outsider(self):
        """Deny contributing-order access outside the actor's scope."""
        outsider = cohort_demo_outsider(self.client_tenant)
        response = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/work-orders/',
            user=outsider,
        )
        self.assertEqual(response.status_code, 403)

    def test_location_api_demo_session_filter(self):
        """The location summary and machine list narrow to the session cohort."""
        session_id = self.session.pk
        # A machine outside the session must not contaminate demo-filtered rows.
        from assets.models import AssetMachine

        AssetMachine.objects.create(
            name='Operator spare',
            client=self.client_tenant,
            physical_location=self.locations['SITE-A'],
        )
        unfiltered = self.get(
            f'/api/assets/locations/{self.locations["SITE-A"].pk}/', user=self.actor
        )
        self.assertEqual(unfiltered.data['counts']['total_machines'], 4)
        filtered = self.get(
            f'/api/assets/locations/{self.locations["SITE-A"].pk}/'
            f'?demo_session={session_id}',
            user=self.actor,
        )
        self.assertEqual(filtered.data['counts']['total_machines'], 3)
        machines = self.get(
            f'/api/assets/locations/machines/?demo_session={session_id}',
            user=self.actor,
        )
        self.assertEqual(machines.data['count'], 6)
        outsider = cohort_demo_outsider(self.client_tenant)
        denied = self.get(
            f'/api/assets/locations/machines/?demo_session={session_id}', user=outsider
        )
        self.assertEqual(denied.status_code, 403)

    def test_location_api_demo_session_filter_never_widens(self):
        """An unknown session id is not authority and not an ignored filter."""
        response = self.get(
            '/api/assets/locations/machines/?demo_session=not-a-uuid', user=self.actor
        )
        self.assertEqual(response.status_code, 400)
        response = self.get(
            '/api/assets/locations/machines/'
            '?demo_session=00000000-0000-0000-0000-000000000000',
            user=self.actor,
        )
        self.assertEqual(response.status_code, 404)


@requires_postgres
class CleanedSessionScopeTest(DemoMetricsEnvMixin, TestCase):
    """Authority over the cohort spans ALL retained memberships.

    Regression for the cleaned-session leak: ``session_cohort`` judged denial
    by the existence of *active* claims only, so a CLEANED session — the real
    cleanup service retains every membership with ``claim_active=False`` —
    handed zeros plus session metadata to foreign scoped actors on metrics,
    work-orders, history and the shared ``demo_session`` membership call
    sites. Authority is proven only through retained session membership
    (active claim or not) and is never invented from the caller or the
    demo-owner evidence; the empty projection is reserved for actors whose
    authority over retained membership is real.
    """

    def setUp(self):
        """Apply a session with imported history; each test decides lifecycle."""
        self.build_demo_env(history_import_approved=True)
        self.result = self.apply_demo(include_history=True)
        self.session = self.session()
        self.outsider = cohort_demo_outsider(self.client_tenant)
        self.start = translated_window(self.fixture, self.session, 'start_at')
        self.end = translated_window(self.fixture, self.session, 'end_at')

    def get(self, url, user):
        """Read an endpoint as the requested actor."""
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=user)
        return client.get(url)

    def surfaces(self, user):
        """Every shared cohort surface: three session APIs plus membership."""
        pk = self.session.pk
        return {
            'metrics': self.get(
                f'/api/assets/demo-metrics/sessions/{pk}/metrics/', user
            ),
            'work-orders': self.get(
                f'/api/assets/demo-metrics/sessions/{pk}/work-orders/', user
            ),
            'history': self.get(
                f'/api/assets/demo-metrics/sessions/{pk}/history/'
                f'?start={reference.iso(self.start)}&end={reference.iso(self.end)}',
                user,
            ),
            'machines': self.get(
                f'/api/assets/locations/machines/?demo_session={pk}', user
            ),
            'location': self.get(
                f'/api/assets/locations/{self.locations["SITE-A"].pk}/'
                f'?demo_session={pk}',
                user,
            ),
        }

    def clean_up_session(self):
        """Run the actual cleanup service (claims flip; memberships retained)."""
        plan = cleanup.build_cleanup_plan(self.session, self.actor)
        cleanup.apply_cleanup(
            self.session, self.actor, approved_cleanup_sha256=plan['plan_hash']
        )

    def test_active_baseline_serves_the_cohort_and_denies_the_outsider(self):
        """Active baseline: full projection for authority, 403 for outsiders."""
        served = self.surfaces(self.actor)
        self.assertEqual(served['metrics'].status_code, 200)
        self.assertEqual(served['metrics'].data['cohort_size'], 6)
        self.assertEqual(served['work-orders'].status_code, 200)
        self.assertEqual(served['work-orders'].data['count'], 9)
        self.assertEqual(served['history'].status_code, 200)
        self.assertTrue(served['history'].data['available'])
        self.assertEqual(served['machines'].status_code, 200)
        self.assertEqual(served['machines'].data['count'], 6)
        self.assertEqual(served['location'].status_code, 200)
        for label, response in self.surfaces(self.outsider).items():
            self.assertEqual(response.status_code, 403, label)

    def test_stopped_lifecycle_keeps_the_authority_boundary(self):
        """A stopped session keeps its claims: outsiders stay denied."""
        replay.stop_session(self.session, self.actor)
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        self.assertEqual(session.status, DemoMetricsSession.Status.STOPPED)
        self.assertTrue(session.machines.filter(claim_active=True).exists())
        served = self.surfaces(self.actor)
        self.assertEqual(served['metrics'].status_code, 200)
        self.assertEqual(served['metrics'].data['cohort_size'], 6)
        self.assertEqual(served['history'].status_code, 200)
        for label, response in self.surfaces(self.outsider).items():
            self.assertEqual(response.status_code, 403, label)

    def test_cleaned_session_denies_the_outsider_on_every_cohort_surface(self):
        """A foreign scoped actor is 403 after cleanup, never zeroed."""
        self.clean_up_session()
        session = DemoMetricsSession.objects.get(pk=self.session.pk)
        self.assertEqual(session.status, DemoMetricsSession.Status.CLEANED)
        # The cleanup service retains every membership as an inactive claim.
        self.assertEqual(session.machines.count(), 6)
        self.assertFalse(session.machines.filter(claim_active=True).exists())
        for label, response in self.surfaces(self.outsider).items():
            self.assertEqual(response.status_code, 403, label)
        # The shared cohort primitives deny exactly as on an active session.
        for call in (
            lambda: cohort.session_cohort(self.session, self.outsider),
            lambda: cohort.session_membership_ids(self.session, self.outsider),
            lambda: cohort.session_orders(self.session, self.outsider),
            lambda: cohort.session_current_metrics(self.session, self.outsider),
        ):
            with self.assertRaises(cohort.CohortError) as caught:
                call()
            self.assertEqual(caught.exception.code, 'SCOPE_DENIED')

    def test_authorized_cleaned_readback_is_an_explicit_empty_projection(self):
        """The authorized operator reads the cleaned session back, honestly."""
        self.clean_up_session()
        self.session.refresh_from_db()
        metrics = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/metrics/', self.actor
        )
        self.assertEqual(metrics.status_code, 200)
        self.assertEqual(metrics.data['cohort_size'], 0)
        self.assertEqual(metrics.data['machines'], [])
        self.assertEqual(metrics.data['open_work_orders'], 0)
        self.assertEqual(
            metrics.data['session']['status'], DemoMetricsSession.Status.CLEANED
        )
        self.assertEqual(
            metrics.data['session']['session_key'], self.session.session_key
        )
        self.assertFalse(metrics.data['capabilities']['replay_available'])
        work = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/work-orders/',
            self.actor,
        )
        self.assertEqual(work.status_code, 200)
        self.assertEqual(work.data['count'], 0)
        self.assertEqual(work.data['open_count'], 0)
        history_response = self.get(
            f'/api/assets/demo-metrics/sessions/{self.session.pk}/history/'
            f'?start={reference.iso(self.start)}&end={reference.iso(self.end)}',
            self.actor,
        )
        self.assertEqual(history_response.status_code, 200)
        if history_response.data.get('available'):
            self.assertEqual(history_response.data['selected_machines'], 0)
            self.assertIsNone(history_response.data['downtime_machine_minutes'])
        machines = self.get(
            f'/api/assets/locations/machines/?demo_session={self.session.pk}',
            self.actor,
        )
        self.assertEqual(machines.status_code, 200)
        self.assertEqual(machines.data['count'], 0)
        # The shared primitives agree on the explicit empty active projection.
        self.assertEqual(list(cohort.session_cohort(self.session, self.actor)), [])
        self.assertEqual(cohort.session_membership_ids(self.session, self.actor), set())

    def test_membership_less_legacy_session_is_denied_without_invented_ownership(self):
        """A session with no retained membership proves nobody's authority.

        A legacy/membership-less session carries no evidence tying any actor
        to it; the demo-owner FK is evidence, never authority. Both the
        in-scope operator and the foreign scoped actor are denied explicitly
        — no zeroed projection is handed out and no ownership is invented.
        """
        import uuid as uuid_module

        legacy = DemoMetricsSession.objects.get(pk=self.session.pk)
        legacy.id = uuid_module.uuid4()
        legacy.dataset_key = 'equa-legacy-dataset'
        legacy.session_key = 'equa-legacy-empty-1'
        legacy.demo_owner = self.actor
        legacy.save()
        self.assertFalse(legacy.machines.exists())
        for user in (self.actor, self.outsider):
            response = self.get(
                f'/api/assets/demo-metrics/sessions/{legacy.pk}/metrics/', user
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.data['error'], 'SCOPE_DENIED')
            with self.assertRaises(cohort.CohortError) as caught:
                cohort.session_cohort(legacy, user)
            self.assertEqual(caught.exception.code, 'SCOPE_DENIED')


@requires_postgres
class HealthScopeRegressionTest(DemoMetricsEnvMixin, TestCase):
    """The machine-health parent lookup is explicitly client-scoped."""

    def setUp(self):
        """Prepare health state and an actor from another client."""
        self.build_demo_env()
        self.apply_demo()
        self.session = self.session()
        self.outsider = cohort_demo_outsider(self.client_tenant)

    def get(self, url, user):
        """Read a machine-health endpoint as the specified actor."""
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=user)
        return client.get(url)

    def test_in_scope_actor_reads_machine_health(self):
        """Allow a scoped actor to read the parent machine's health."""
        response = self.get(
            f'/api/machine-health/machines/{self.machines["A01"].pk}/health/',
            self.actor,
        )
        self.assertEqual(response.status_code, 200)

    def test_out_of_scope_actor_is_denied_not_empty(self):
        """Reject out-of-scope health requests rather than returning empties."""
        response = self.get(
            f'/api/machine-health/machines/{self.machines["A01"].pk}/health/',
            self.outsider,
        )
        self.assertIn(response.status_code, (403, 404))

    def test_unscoped_machine_is_denied(self):
        """A machine without a client is unreachable — no legacy pass-through."""
        machine = self.machines['B03']
        machine.client = None
        machine.save(update_fields=['client'])
        response = self.get(
            f'/api/machine-health/machines/{machine.pk}/health/', self.outsider
        )
        self.assertEqual(response.status_code, 404)
        response = self.get(
            f'/api/machine-health/machines/{machine.pk}/health/', self.actor
        )
        self.assertEqual(response.status_code, 404)

    def test_role_grants_alone_do_not_authorize_health(self):
        """Privileged but scope-unresolved actors are denied, not served."""
        unscoped = cohort_demo_unscoped_user()
        response = self.get(
            f'/api/machine-health/machines/{self.machines["A01"].pk}/health/', unscoped
        )
        self.assertEqual(response.status_code, 404)


@requires_postgres
class HistoryWindowAggregationTest(DemoMetricsEnvMixin, TestCase):
    """UTC day boundaries, clipping, union and missing coverage.

    The fixture covers 14 complete UTC days: A02 and B01 each observed
    08:00-16:00 daily (960 planned machine-minutes per day together), with
    410 downtime machine-minutes in total.
    """

    def setUp(self):
        """Import a session with measured historical coverage."""
        self.build_demo_env(history_import_approved=True)
        self.result = self.apply_demo(include_history=True)
        self.session = self.session()

    def aggregate(self, start, end, machine_ids=None):
        """Aggregate a reference window translated to the session anchor."""
        return history.aggregate_history(
            session=self.session,
            start=translated_instant(self.fixture, self.session, start),
            end=translated_instant(self.fixture, self.session, end),
            machine_ids=machine_ids,
        )

    def test_daily_buckets_are_utc_days(self):
        """Use contiguous UTC dates for translated daily buckets."""
        window = self.aggregate('2026-09-10T00:00:00Z', '2026-09-24T00:00:00Z')
        dates = [bucket['date'] for bucket in window['daily']]
        # Buckets are consecutive UTC calendar days (the anchor shift may
        # widen the window by a partial day at each end, never more).
        self.assertIn(len(dates), (14, 15))
        parsed = [dt.date.fromisoformat(value) for value in dates]
        for previous, current in pairwise(parsed):
            self.assertEqual(current - previous, dt.timedelta(days=1))
        for bucket in window['daily']:
            # Full observed days carry their planned share; missing coverage
            # is never invented.
            self.assertGreaterEqual(bucket['planned_machine_minutes'], 0)
            if bucket['planned_machine_minutes']:
                self.assertIsNotNone(bucket['downtime_machine_minutes'])
        self.assertEqual(
            sum(bucket['planned_machine_minutes'] for bucket in window['daily']), 13440
        )
        self.assertAlmostEqual(window['planned_machine_minutes'], 13440)
        self.assertAlmostEqual(window['downtime_machine_minutes'], 410)

    def test_partial_first_day_is_clipped_to_the_window(self):
        """Clip first-day coverage to the requested window."""
        window = self.aggregate('2026-09-10T12:00:00Z', '2026-09-11T00:00:00Z')
        # Two machines observed 12:00-16:00 only: half of the full 960.
        self.assertAlmostEqual(window['planned_machine_minutes'], 480)
        self.assertEqual(window['downtime_machine_minutes'], 0)
        self.assertEqual(
            sum(bucket['planned_machine_minutes'] for bucket in window['daily']), 480
        )

    def test_partial_window_clips_downtime_and_coverage_together(self):
        """Clip losses and their denominator to the same window."""
        # A02's 11:00-11:20 loss is clipped to 11:10-11:20; coverage is
        # clipped to 11:10-16:00 for both machines.
        window = self.aggregate('2026-09-11T11:10:00Z', '2026-09-12T00:00:00Z')
        self.assertAlmostEqual(window['planned_machine_minutes'], 580)
        self.assertAlmostEqual(window['downtime_machine_minutes'], 10)
        self.assertEqual(
            sum(bucket['downtime_machine_minutes'] or 0 for bucket in window['daily']),
            10,
        )

    def test_overlapping_downtime_is_unioned_not_double_counted(self):
        """Union overlapping losses before summing machine-minutes."""
        machine = self.machines['A01']
        DemoMetricsDowntimeInterval.objects.create(
            session=self.session,
            event_key='test-overlap-1',
            machine=machine,
            start_at=translated_instant(
                self.fixture, self.session, '2026-09-10T09:00:00Z'
            ),
            end_at=translated_instant(
                self.fixture, self.session, '2026-09-10T10:00:00Z'
            ),
            loss_category='unplanned_loss_of_function',
            attribution_mode=history.ATTRIBUTION_MODE,
        )
        DemoMetricsDowntimeInterval.objects.create(
            session=self.session,
            event_key='test-overlap-2',
            machine=machine,
            start_at=translated_instant(
                self.fixture, self.session, '2026-09-10T09:30:00Z'
            ),
            end_at=translated_instant(
                self.fixture, self.session, '2026-09-10T10:30:00Z'
            ),
            loss_category='unplanned_loss_of_function',
            attribution_mode=history.ATTRIBUTION_MODE,
        )
        # A01 has no planned coverage: its downtime is clipped away entirely.
        window = self.aggregate('2026-09-10T00:00:00Z', '2026-09-11T00:00:00Z')
        self.assertEqual(window['downtime_machine_minutes'], 0)
        # On A02 (covered) two overlapping losses union to 90 minutes, not 120.
        # The fresh UTC anchor can split intervals across day buckets, so the
        # summed minute floats carry roundoff; the union itself is exact.
        machine_a02 = self.machines['A02']
        DemoMetricsDowntimeInterval.objects.create(
            session=self.session,
            event_key='test-overlap-3',
            machine=machine_a02,
            start_at=translated_instant(
                self.fixture, self.session, '2026-09-10T09:00:00Z'
            ),
            end_at=translated_instant(
                self.fixture, self.session, '2026-09-10T10:00:00Z'
            ),
            loss_category='unplanned_loss_of_function',
            attribution_mode=history.ATTRIBUTION_MODE,
        )
        DemoMetricsDowntimeInterval.objects.create(
            session=self.session,
            event_key='test-overlap-4',
            machine=machine_a02,
            start_at=translated_instant(
                self.fixture, self.session, '2026-09-10T09:30:00Z'
            ),
            end_at=translated_instant(
                self.fixture, self.session, '2026-09-10T10:30:00Z'
            ),
            loss_category='unplanned_loss_of_function',
            attribution_mode=history.ATTRIBUTION_MODE,
        )
        window = self.aggregate('2026-09-10T00:00:00Z', '2026-09-11T00:00:00Z')
        self.assertAlmostEqual(window['downtime_machine_minutes'], 90)
        self.assertAlmostEqual(
            sum(bucket['downtime_machine_minutes'] or 0 for bucket in window['daily']),
            90,
        )

    def test_missing_coverage_is_null_not_zero(self):
        """Keep missing coverage unknown in empty and mixed windows."""
        # Days before any coverage exists: nulls, never zero loss.
        window = self.aggregate('2026-09-08T00:00:00Z', '2026-09-10T00:00:00Z')
        for bucket in window['daily']:
            self.assertEqual(bucket['planned_machine_minutes'], 0)
            self.assertIsNone(bucket['downtime_machine_minutes'])
        self.assertEqual(window['planned_machine_minutes'], 0)
        self.assertIsNone(window['downtime_machine_minutes'])
        self.assertIsNone(window['measured_cohort_availability'])
        self.assertEqual(window['measured_machines'], 0)
        self.assertEqual(window['selected_machines'], 6)
        self.assertEqual(window['completeness'], 'partial_measured_cohort')
        # A mixed window keeps the distinction per bucket: uncovered buckets
        # stay null, fully observed buckets with no loss report zero.
        mixed = self.aggregate('2026-09-09T00:00:00Z', '2026-09-11T00:00:00Z')
        values = [bucket['downtime_machine_minutes'] for bucket in mixed['daily']]
        self.assertIn(None, values)
        self.assertIn(0, values)
        # The fresh anchor can split coverage across UTC day boundaries;
        # summing fractional bucket minutes introduces float roundoff.
        self.assertAlmostEqual(mixed['planned_machine_minutes'], 960)

    def test_half_open_boundaries_do_not_touch_neighbors(self):
        """Exclude adjacent intervals at half-open window boundaries."""
        # Exactly between two coverage windows: [16:00, 08:00) counts nothing.
        window = self.aggregate('2026-09-10T16:00:00Z', '2026-09-11T08:00:00Z')
        self.assertEqual(window['planned_machine_minutes'], 0)
        self.assertIsNone(window['downtime_machine_minutes'])


def cohort_demo_unscoped_user():
    """A privileged actor whose maintenance scope cannot be resolved at all.

    A superuser with no ``maintenance_scopes`` and no configured resolver:
    every denial observed with this actor proves scope enforcement, and that
    role/superuser grants alone never substitute for it.
    """
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_superuser(
        username='unscoped', email='unscoped@example.com', password='pw'
    )


def cohort_demo_outsider(client_tenant):
    """A privileged actor whose maintenance scope excludes the demo tenant.

    A superuser so every denial observed in these tests is a *scope* denial and
    never a role artifact — scope must constrain even privileged users.
    """
    from django.contrib.auth import get_user_model

    outsider = get_user_model().objects.create_superuser(
        username='outsider', email='o@example.com', password='pw'
    )
    outsider.maintenance_scopes = {
        MaintenanceScope(
            customer_id=None, site_key=None, client_id=client_tenant.pk + 1
        )
    }
    return outsider
