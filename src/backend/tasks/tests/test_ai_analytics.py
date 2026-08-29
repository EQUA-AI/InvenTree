"""S7 complete-population analytics: scope, vocabulary, honest coverage.

The WP-C1 surface: the maintenance-record scope helpers, the dataset
profile, the single-dimension aggregate, and the ``date_field`` selector on
the conversational page. Populations are never unioned; groupings are an
allow-list; every result names its clock.
"""

from __future__ import annotations

import datetime
import unittest
import uuid

from django.apps import apps

if not apps.is_installed('assets'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings

from assets.models import AssetMachine, AssetMaintenanceRecord, Client
from company.models import Company
from tasks import ai_analytics, ai_read
from tasks.models import WorkOrder
from tasks.scope import (
    MaintenanceScope,
    ScopeError,
    maintenance_record_scope_filter,
    require_maintenance_record_scope,
)

#: The read flag plus a pinned plant clock, so window tests are
#: deterministic regardless of the dev machine's INVENTREE_TIMEZONE.
READ_FLAGS = {
    'AIMMS_MAINTENANCE_AI_READ_ENABLED': True,
    'AIMMS_PLANT_TIMEZONE': 'UTC',
}

UTC = datetime.UTC


def _dt(year, month, day, hour=0, minute=0):
    """A UTC instant in the storage convention the runner uses.

    The InvenTree test runner flips ``USE_TZ`` off (see ``tz_support``), so
    fixtures follow the setting: naive values under the runner's UTC clock,
    aware values in a suite that overrides ``USE_TZ=True``.
    """
    value = datetime.datetime(year, month, day, hour, minute, tzinfo=UTC)
    if not settings.USE_TZ:
        return value.replace(tzinfo=None)
    return value


@override_settings(**READ_FLAGS)
class AnalyticsTestCase(TestCase):
    """Two tenants, a sales customer, machines and a dated work-order graph."""

    @classmethod
    def setUpTestData(cls):
        """Create the scoped analytics graph once."""
        suffix = uuid.uuid4().hex[:6]
        cls.customer = Company.objects.create(
            name=f'Analytics Customer {suffix}', is_customer=True
        )
        cls.client_tenant = Client.objects.create(
            name=f'Plant A {suffix}', code=f'an-a-{suffix}'
        )
        cls.other_client = Client.objects.create(
            name=f'Plant B {suffix}', code=f'an-b-{suffix}'
        )

        users = get_user_model().objects
        cls.actor = users.create_superuser(
            username=f'an-actor-{suffix}', email='aa@example.com', password='pw'
        )
        cls.outsider = users.create_superuser(
            username=f'an-outsider-{suffix}', email='ao@example.com', password='pw'
        )
        cls.customer_actor = users.create_superuser(
            username=f'an-customer-{suffix}', email='ac@example.com', password='pw'
        )
        cls.unresolved = users.create_superuser(
            username=f'an-unresolved-{suffix}', email='au@example.com', password='pw'
        )

        cls.machine_one = AssetMachine.objects.create(
            name=f'Feed Pump {suffix}', client=cls.client_tenant
        )
        cls.machine_two = AssetMachine.objects.create(
            name=f'Inverter Hall {suffix}', client=cls.client_tenant
        )
        cls.foreign_machine = AssetMachine.objects.create(
            name=f'Foreign Press {suffix}', client=cls.other_client
        )

        cls.wo_jan = cls._work_order(
            title='January corrective',
            machine=cls.machine_one,
            priority=WorkOrder.PRIORITY_HIGH,
            lifecycle_status='completed',
            affected_component_ref='INV-7',
            actual_started_at=_dt(2026, 1, 15, 1),
            actual_completed_at=_dt(2026, 1, 15, 3),
        )
        cls.wo_open = cls._work_order(
            title='Open preventive',
            machine=cls.machine_one,
            work_order_type='preventive',
        )
        cls.wo_feb = cls._work_order(
            title='February corrective',
            machine=cls.machine_two,
            priority=WorkOrder.PRIORITY_LOW,
            lifecycle_status='completed',
            affected_component_ref='INV-7',
            actual_completed_at=_dt(2026, 2, 10, 9),
        )
        cls.customer_wo = cls._work_order(
            title='Customer callout with machine',
            customer=cls.customer,
            machine=cls.foreign_machine,
        )
        cls.customer_unassigned_wo = cls._work_order(
            title='Customer callout, no machine yet', customer=cls.customer
        )
        cls.foreign_wo = cls._work_order(
            title='Foreign press overhaul', machine=cls.foreign_machine
        )

        cls.linked_record = AssetMaintenanceRecord.objects.create(
            machine=cls.machine_one,
            date=datetime.date(2026, 1, 15),
            summary='Closed out from WO',
            work_order=cls.wo_jan,
        )
        cls.foreign_record = AssetMaintenanceRecord.objects.create(
            machine=cls.foreign_machine,
            date=datetime.date(2026, 3, 1),
            summary='Foreign record',
        )

    @classmethod
    def _work_order(cls, **overrides):
        values = {
            'title': 'Work',
            'status': WorkOrder.STATUS_BACKLOG,
            'priority': WorkOrder.PRIORITY_MEDIUM,
        }
        values.update(overrides)
        return WorkOrder.objects.create(**values)

    def setUp(self):
        """Grant each actor its boundary through the attribute seam."""
        self.actor.maintenance_scopes = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_tenant.pk
            )
        }
        self.outsider.maintenance_scopes = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.other_client.pk
            )
        }
        self.customer_actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }


class MaintenanceRecordScopeTests(AnalyticsTestCase):
    """The record filter is the set form of the per-record check."""

    def test_filter_never_selects_what_require_would_deny(self):
        """The set form and the per-record form agree for every actor."""
        for username, actor in (
            ('actor', self.actor),
            ('outsider', self.outsider),
            ('customer', self.customer_actor),
        ):
            with self.subTest(actor=username):
                selected = set(
                    AssetMaintenanceRecord.objects.filter(
                        maintenance_record_scope_filter(actor)
                    )
                )
                for record in AssetMaintenanceRecord.objects.all():
                    try:
                        require_maintenance_record_scope(actor, record)
                        self.assertIn(record, selected)
                    except ScopeError:
                        self.assertNotIn(record, selected)

    def test_customer_scope_reaches_no_record(self):
        """A customer is a claim about a work order, never about history."""
        rows = AssetMaintenanceRecord.objects.filter(
            maintenance_record_scope_filter(self.customer_actor)
        )
        self.assertEqual(rows.count(), 0)

    def test_linked_work_order_never_widens_the_record(self):
        """The record rides its machine's client even when its WO is reachable."""
        self.assertEqual(
            require_maintenance_record_scope(self.actor, self.linked_record).client_id,
            self.client_tenant.pk,
        )
        with self.assertRaises(ScopeError):
            require_maintenance_record_scope(self.outsider, self.linked_record)


class UnavailableShapeTests(AnalyticsTestCase):
    """Fail-closed shapes must abstain, never validate a false zero."""

    def _assert_unavailable(self, result):
        self.assertFalse(result['available'])
        self.assertEqual(result['population_count'], 0)
        self.assertFalse(result['complete_population'])

    @override_settings(AIMMS_MAINTENANCE_AI_READ_ENABLED=False)
    def test_flag_off_is_unavailable_not_zero(self):
        """A dark read surface is 'unavailable', never 'zero work orders'."""
        self._assert_unavailable(
            ai_analytics.get_work_order_dataset_profile(self.actor)
        )
        self._assert_unavailable(
            ai_analytics.aggregate_work_orders(self.actor, grouping='machine')
        )

    def test_anonymous_and_unresolved_are_unavailable(self):
        """No authenticated identity or no resolved scope: same silence."""
        self._assert_unavailable(
            ai_analytics.get_work_order_dataset_profile(AnonymousUser())
        )
        self._assert_unavailable(
            ai_analytics.aggregate_work_orders(self.unresolved, grouping='machine')
        )


class DatasetProfileTests(AnalyticsTestCase):
    """The profile describes the complete authorized population honestly."""

    def test_profile_counts_only_the_actors_population(self):
        """Three client jobs for the actor; the foreign and customer jobs stay out."""
        profile = ai_analytics.get_work_order_dataset_profile(self.actor)
        self.assertTrue(profile['available'])
        self.assertEqual(profile['population_count'], 3)
        self.assertTrue(profile['complete_population'])
        self.assertEqual(profile['population_type'], 'work_orders')
        self.assertEqual(profile['unassigned_machine_count'], 0)
        self.assertEqual(profile['distinct_machine_count'], 2)
        self.assertEqual(profile['work_order_type_counts']['preventive'], 1)
        self.assertEqual(profile['lifecycle_status_counts']['completed'], 2)
        self.assertEqual(profile['applied_filters']['date_field'], 'created_at')
        self.assertIsNotNone(profile['high_watermark'])

    def test_null_dates_are_counted_not_dropped(self):
        """Profiling on completion still accounts for the never-completed."""
        profile = ai_analytics.get_work_order_dataset_profile(
            self.actor, date_field='actual_completed_at'
        )
        self.assertEqual(profile['population_count'], 3)
        self.assertEqual(profile['null_date_count'], 1)
        self.assertEqual(profile['date_min'], _dt(2026, 1, 15, 3).isoformat())
        self.assertEqual(profile['date_max'], _dt(2026, 2, 10, 9).isoformat())

    def test_unassigned_machine_count_surfaces_for_customer_scope(self):
        """Q17: machineless jobs are counted, and never join an asset group."""
        profile = ai_analytics.get_work_order_dataset_profile(self.customer_actor)
        self.assertEqual(profile['population_count'], 2)
        self.assertEqual(profile['unassigned_machine_count'], 1)

    def test_linked_record_never_inflates_the_work_order_population(self):
        """A7: the closeout-created record enriches, it is not a second event."""
        profile = ai_analytics.get_work_order_dataset_profile(self.actor)
        self.assertEqual(profile['population_count'], 3)
        records = AssetMaintenanceRecord.objects.filter(
            maintenance_record_scope_filter(self.actor)
        )
        self.assertEqual(records.count(), 1)


class AggregateTests(AnalyticsTestCase):
    """Server-side grouping over the complete population."""

    def test_machine_grouping_counts_and_fences_labels(self):
        """Groups carry ids, fenced labels and server counts."""
        result = ai_analytics.aggregate_work_orders(self.actor, grouping='machine')
        self.assertTrue(result['available'])
        self.assertEqual(result['population_count'], 3)
        self.assertTrue(result['complete_population'])
        by_key = {row['key']: row for row in result['groups']}
        self.assertEqual(by_key[self.machine_one.pk]['group_count'], 2)
        self.assertEqual(by_key[self.machine_two.pk]['group_count'], 1)
        self.assertIn(
            ai_read.UNTRUSTED_CONTENT_BEGIN, by_key[self.machine_one.pk]['label']
        )
        self.assertEqual(result['remainder_count'], 0)
        self.assertFalse(result['groups_truncated'])

    def test_machine_grouping_excludes_unassigned_and_reports_them(self):
        """Q17 for the aggregate: no inferred asset, an explicit count instead."""
        result = ai_analytics.aggregate_work_orders(
            self.customer_actor, grouping='machine'
        )
        self.assertEqual(result['unassigned_machine_count'], 1)
        self.assertEqual(result['population_count'], 1)
        self.assertEqual(len(result['groups']), 1)

    def test_component_ref_grouping_uses_exact_values(self):
        """Exact recorded refs group; blanks group together as blank."""
        result = ai_analytics.aggregate_work_orders(
            self.actor, grouping='component_ref'
        )
        by_key = {row['key']: row['group_count'] for row in result['groups']}
        self.assertEqual(by_key['INV-7'], 2)
        self.assertEqual(by_key[''], 1)

    def test_free_text_and_identity_groupings_are_structurally_absent(self):
        """The allow-list is the control: no enum member, no grouping."""
        for forbidden in ('performed_by', 'description', 'assigned_to', 'title'):
            with self.subTest(grouping=forbidden):
                with self.assertRaises(ai_analytics.AnalyticsRequestError) as caught:
                    ai_analytics.aggregate_work_orders(self.actor, grouping=forbidden)
                self.assertEqual(caught.exception.code, 'grouping_unavailable')

    def test_unapproved_date_field_is_typed(self):
        """`updated_at` is a bookkeeping clock, not an event clock."""
        with self.assertRaises(ai_analytics.AnalyticsRequestError) as caught:
            ai_analytics.aggregate_work_orders(
                self.actor, grouping='machine', date_field='updated_at'
            )
        self.assertEqual(caught.exception.code, 'date_field_unavailable')

    def test_window_is_half_open_on_the_selected_field(self):
        """[from, to): the completion on `to` day's midnight stays out."""
        result = ai_analytics.aggregate_work_orders(
            self.actor,
            grouping='machine',
            date_field='actual_completed_at',
            date_from='2026-01-15',
            date_to='2026-02-10',
        )
        self.assertEqual(result['population_count'], 1)
        self.assertEqual(result['groups'][0]['key'], self.machine_one.pk)
        self.assertEqual(result['applied_filters']['from'], '2026-01-15')
        self.assertEqual(result['applied_filters']['to'], '2026-02-10')

    def test_invalid_window_is_typed(self):
        """Garbage dates and empty windows are vocabulary errors, not guesses."""
        for kwargs in (
            {'date_from': 'not-a-date'},
            {'date_from': '2026-02-01', 'date_to': '2026-01-01'},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ai_analytics.AnalyticsRequestError) as caught:
                    ai_analytics.aggregate_work_orders(
                        self.actor, grouping='machine', **kwargs
                    )
                self.assertEqual(caught.exception.code, 'window_invalid')

    @override_settings(AIMMS_PLANT_TIMEZONE='America/New_York')
    def test_window_uses_the_plant_clock(self):
        """03:00Z on Jan 15 is Jan 14 in New York: the plant window excludes it."""
        result = ai_analytics.aggregate_work_orders(
            self.actor,
            grouping='machine',
            date_field='actual_completed_at',
            date_from='2026-01-15',
            date_to='2026-02-01',
        )
        self.assertEqual(result['population_count'], 0)
        self.assertEqual(result['timezone'], 'America/New_York')

    @override_settings(AIMMS_PLANT_TIMEZONE='Not/AZone')
    def test_bad_timezone_knob_falls_back_visibly(self):
        """A broken knob narrows to the server clock and says which it used."""
        result = ai_analytics.aggregate_work_orders(self.actor, grouping='machine')
        self.assertEqual(result['timezone'], str(settings.TIME_ZONE))


class GroupCapTests(TestCase):
    """The hard cap bounds every grouped answer with an honest remainder."""

    @classmethod
    def setUpTestData(cls):
        suffix = uuid.uuid4().hex[:6]
        cls.tenant = Client.objects.create(
            name=f'Cap Plant {suffix}', code=f'cap-{suffix}'
        )
        cls.machine = AssetMachine.objects.create(
            name=f'Cap Machine {suffix}', client=cls.tenant
        )
        cls.actor = get_user_model().objects.create_superuser(
            username=f'cap-actor-{suffix}', email='cap@example.com', password='pw'
        )
        cls.total = ai_analytics.HARD_GROUP_CAP + 6
        for index in range(cls.total):
            WorkOrder.objects.create(
                title=f'Cap job {index}',
                status=WorkOrder.STATUS_BACKLOG,
                priority=WorkOrder.PRIORITY_MEDIUM,
                machine=cls.machine,
                affected_component_ref=f'REF-{index:03d}',
            )

    def setUp(self):
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        }

    @override_settings(**READ_FLAGS)
    def test_overflow_collapses_into_a_server_counted_remainder(self):
        """Beyond the cap the table ends in one counted remainder row."""
        result = ai_analytics.aggregate_work_orders(
            self.actor, grouping='component_ref'
        )
        self.assertEqual(result['population_count'], self.total)
        self.assertEqual(result['total_group_count'], self.total)
        self.assertEqual(len(result['groups']), ai_analytics.HARD_GROUP_CAP)
        self.assertTrue(result['groups_truncated'])
        self.assertEqual(result['remainder_group_count'], 6)
        self.assertEqual(result['remainder_count'], 6)


class TimelineTests(AnalyticsTestCase):
    """Zero-filled calendar series with honest gaps."""

    def test_month_series_zero_fills_the_window(self):
        """December exists as a zero, not as silence."""
        result = ai_analytics.get_work_order_timeline(
            self.actor,
            bucket='month',
            date_field='actual_completed_at',
            date_from='2025-12-01',
            date_to='2026-03-01',
        )
        self.assertTrue(result['available'])
        self.assertEqual(
            [(row['bucket'], row['group_count']) for row in result['buckets']],
            [('2025-12-01', 0), ('2026-01-01', 1), ('2026-02-01', 1)],
        )
        self.assertEqual(result['population_count'], 2)

    def test_null_event_dates_are_counted_outside_the_series(self):
        """The never-completed order is a count, never a bucket member."""
        result = ai_analytics.get_work_order_timeline(
            self.actor, bucket='month', date_field='actual_completed_at'
        )
        self.assertEqual(result['null_date_count'], 1)
        self.assertEqual(result['population_count'], 3)
        self.assertEqual(sum(row['group_count'] for row in result['buckets']), 2)

    def test_maintenance_record_series_uses_the_record_date(self):
        """The records population buckets by its own calendar date."""
        result = ai_analytics.get_work_order_timeline(
            self.actor, bucket='month', population='maintenance_records'
        )
        self.assertEqual(result['population_type'], 'maintenance_records')
        self.assertEqual(result['date_field'], 'date')
        self.assertEqual(
            [(row['bucket'], row['group_count']) for row in result['buckets']],
            [('2026-01-01', 1)],
        )

    def test_record_population_rejects_other_clocks(self):
        """`created_at` on records is bookkeeping, not the event date."""
        with self.assertRaises(ai_analytics.AnalyticsRequestError) as caught:
            ai_analytics.get_work_order_timeline(
                self.actor,
                bucket='month',
                population='maintenance_records',
                date_field='created_at',
            )
        self.assertEqual(caught.exception.code, 'date_field_unavailable')

    def test_union_population_is_unsayable(self):
        """There is no 'both'; the vocabulary refuses it."""
        with self.assertRaises(ai_analytics.AnalyticsRequestError) as caught:
            ai_analytics.get_work_order_timeline(
                self.actor, bucket='month', population='both'
            )
        self.assertEqual(caught.exception.code, 'population_unavailable')

    def test_over_long_series_is_a_typed_refusal(self):
        """53 weekly buckets: refuse with the reason, never clip silently."""
        with self.assertRaises(ai_analytics.AnalyticsRequestError) as caught:
            ai_analytics.get_work_order_timeline(
                self.actor,
                bucket='week',
                date_field='actual_completed_at',
                date_from='2025-01-01',
                date_to='2026-01-01',
            )
        self.assertEqual(caught.exception.code, 'bucket_range_exceeded')


class RepeatIntervalTests(AnalyticsTestCase):
    """Per-group consecutive-event gaps with explicit rules."""

    def test_machine_intervals_over_completions(self):
        """Three completions on one machine yield two exact gaps."""
        for day, hour in ((1, 3), (11, 3)):
            self._work_order(
                title=f'Repeat corrective {day}',
                machine=self.machine_one,
                lifecycle_status='completed',
                actual_completed_at=_dt(2026, 2, day, hour),
            )
        result = ai_analytics.get_repeat_intervals(
            self.actor, grouping='machine', date_field='actual_completed_at'
        )
        self.assertTrue(result['available'])
        by_key = {row['key']: row for row in result['groups']}
        pump = by_key[self.machine_one.pk]
        self.assertEqual(pump['event_count'], 3)
        self.assertEqual(pump['interval_count'], 2)
        self.assertEqual(pump['min_days'], 10.0)
        self.assertEqual(pump['max_days'], 17.0)
        self.assertEqual(pump['median_days'], 13.5)
        singleton = by_key[self.machine_two.pk]
        self.assertEqual(singleton['event_count'], 1)
        self.assertEqual(singleton['interval_count'], 0)
        self.assertIsNone(singleton['median_days'])

    def test_record_population_intervals_use_calendar_days(self):
        """Maintenance-record gaps are whole calendar days."""
        AssetMaintenanceRecord.objects.create(
            machine=self.machine_one,
            date=datetime.date(2026, 1, 25),
            summary='Follow-up service',
        )
        result = ai_analytics.get_repeat_intervals(
            self.actor, grouping='machine', population='maintenance_records'
        )
        row = result['groups'][0]
        self.assertEqual(row['key'], self.machine_one.pk)
        self.assertEqual(row['event_count'], 2)
        self.assertEqual(row['min_days'], 10.0)

    def test_interval_grouping_allow_list_is_narrower(self):
        """Priority groups an aggregate, but recurrence needs identity."""
        with self.assertRaises(ai_analytics.AnalyticsRequestError) as caught:
            ai_analytics.get_repeat_intervals(self.actor, grouping='priority')
        self.assertEqual(caught.exception.code, 'grouping_unavailable')


class DurationTests(AnalyticsTestCase):
    """Both actuals or excluded — with the exclusions counted."""

    def test_only_fully_timed_orders_qualify(self):
        """One 120-minute job; the untimed and half-timed are counted out."""
        result = ai_analytics.get_work_order_durations(self.actor)
        self.assertTrue(result['available'])
        self.assertEqual(result['population_count'], 3)
        self.assertEqual(result['qualifying_count'], 1)
        self.assertEqual(result['excluded_missing_count'], 2)
        self.assertEqual(result['excluded_invalid_count'], 0)
        self.assertEqual(result['min_minutes'], 120.0)
        self.assertEqual(result['max_minutes'], 120.0)

    def test_negative_durations_are_invalid_not_data(self):
        """Completed-before-started is an exclusion, never a negative stat."""
        self._work_order(
            title='Clock skew job',
            machine=self.machine_two,
            actual_started_at=_dt(2026, 3, 1, 10),
            actual_completed_at=_dt(2026, 3, 1, 8),
        )
        result = ai_analytics.get_work_order_durations(self.actor)
        self.assertEqual(result['excluded_invalid_count'], 1)
        self.assertEqual(result['qualifying_count'], 1)
        self.assertEqual(result['mean_minutes'], 120.0)


class ComparisonCandidateTests(AnalyticsTestCase):
    """Deterministic candidate ordering for the S9 gate."""

    def test_default_rule_orders_by_completion_desc(self):
        """Most recent completed corrective first; the list is the loop."""
        result = ai_analytics.select_comparison_candidate(self.actor)
        self.assertTrue(result['available'])
        self.assertEqual(result['rule'], 'most_recent_completed_corrective')
        self.assertEqual(result['candidates'], [self.wo_feb.pk, self.wo_jan.pk])
        self.assertEqual(result['population_count'], 2)

    def test_machine_narrowing_applies(self):
        """A machine-scoped comparison sees only that machine's jobs."""
        result = ai_analytics.select_comparison_candidate(
            self.actor, machine_id=self.machine_one.pk
        )
        self.assertEqual(result['candidates'], [self.wo_jan.pk])

    def test_unknown_rule_is_typed(self):
        """No rule in the table, no candidates — a typed refusal."""
        with self.assertRaises(ai_analytics.AnalyticsRequestError) as caught:
            ai_analytics.select_comparison_candidate(self.actor, rule='best_guess')
        self.assertEqual(caught.exception.code, 'selection_rule_unavailable')


class MaintenanceEvidenceTests(AnalyticsTestCase):
    """The S9 bundle: distinct stages, enrichment scope-checked."""

    def test_bundle_carries_separate_stages(self):
        """Work order, closeout, linked record and presence counts apart."""
        bundle = ai_analytics.get_maintenance_evidence(self.actor, self.wo_jan.pk)
        self.assertTrue(bundle['available'])
        self.assertEqual(bundle['work_order']['work_order_id'], self.wo_jan.pk)
        self.assertIsNone(bundle['closeout'])
        self.assertEqual(
            bundle['maintenance_record']['record_id'], self.linked_record.pk
        )
        self.assertIn(
            ai_read.UNTRUSTED_CONTENT_BEGIN, bundle['maintenance_record']['summary']
        )
        self.assertFalse(bundle['maintenance_record_withheld'])
        self.assertEqual(bundle['procedure_application_count'], 0)
        self.assertEqual(bundle['deviation_count'], 0)

    def test_foreign_work_order_stays_silent(self):
        """Denial is indistinguishable from absence."""
        bundle = ai_analytics.get_maintenance_evidence(self.outsider, self.wo_jan.pk)
        self.assertFalse(bundle['available'])

    def test_record_enrichment_is_withheld_without_record_scope(self):
        """A customer reaches the job, not the foreign machine's history."""
        AssetMaintenanceRecord.objects.create(
            machine=self.foreign_machine,
            date=datetime.date(2026, 3, 5),
            summary='Foreign enrichment',
            work_order=self.customer_wo,
        )
        bundle = ai_analytics.get_maintenance_evidence(
            self.customer_actor, self.customer_wo.pk
        )
        self.assertTrue(bundle['available'])
        self.assertIsNone(bundle['maintenance_record'])
        self.assertTrue(bundle['maintenance_record_withheld'])


class PageDateFieldTests(AnalyticsTestCase):
    """The conversational page's new validated date-field selector."""

    def test_selected_field_filters_and_echoes(self):
        """An enforce-scoped window means the SELECTED clock, and says so."""
        page = ai_read.work_orders_page(
            self.actor,
            scope_machine_ids=[self.machine_one.pk, self.machine_two.pk],
            scope_date_from='2026-02-01',
            scope_date_to='2026-03-01',
            enforce=True,
            date_field='actual_completed_at',
        )
        self.assertEqual(page['applied_filters']['date_field'], 'actual_completed_at')
        self.assertEqual(page['population_count'], 1)
        self.assertEqual(page['rows'][0].pk, self.wo_feb.pk)

    def test_unknown_field_falls_back_to_created_at(self):
        """An unlisted selector can never reach the ORM."""
        page = ai_read.work_orders_page(self.actor, date_field='updated_at')
        self.assertEqual(page['applied_filters']['date_field'], 'created_at')
        self.assertEqual(page['population_count'], 3)
