"""Tests for the Kanban scheduling read surface.

``WorkOrder`` has carried ``scheduled_start``, ``scheduled_end``,
``estimated_minutes``, ``machine`` and ``assigned_to`` since the work-order
foundation, but ``WorkOrderBoardSerializer`` exposed none of them, so the board could
neither read nor write a schedule. These tests cover exposing those fields and the
``min_date`` / ``max_date`` viewport filter the calendar and timeline query with.
"""

from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from assets.models import AssetMachine
from company.models import Company
from tasks.models import WorkOrder, WorkOrderLifecycle, WorkOrderType


def _utc(year, month, day, hour=9, minute=0):
    """Build a fixed instant with the awareness the database expects.

    Tests run with ``USE_TZ`` false. PostgreSQL tolerates an aware datetime in
    that mode; SQLite refuses it outright, so a hardcoded UTC value made these
    suites pass on one engine and error on the other. Following the setting is
    what makes the same test mean the same thing on both.
    """
    moment = datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)
    return moment if settings.USE_TZ else moment.replace(tzinfo=None)


class KanbanScheduleSerializerTest(TestCase):
    """The serializer round-trips planning metadata and guards the window."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='sched-sup', email='sched@example.com', password='pw'
        )
        self.user.first_name = 'Ada'
        self.user.last_name = 'Lovelace'
        self.user.save(update_fields=['first_name', 'last_name'])
        self.client.force_login(self.user)

        self.customer = Company.objects.create(name='Sched Cust', is_customer=True)
        self.machine = AssetMachine.objects.create(
            name='Press 7', customer=self.customer, location='Bay 4'
        )
        self.work_order = WorkOrder.objects.create(
            title='Replace bearing',
            status='backlog',
            priority='medium',
            machine=self.machine,
            assigned_to=self.user,
            work_order_type=WorkOrderType.PREVENTIVE,
            scheduled_start=_utc(2026, 8, 3),
            scheduled_end=_utc(2026, 8, 3, 13),
            estimated_minutes=240,
        )

    def _detail_url(self):
        return reverse('kanban-card-detail', kwargs={'pk': self.work_order.pk})

    def test_scheduling_fields_are_exposed(self):
        response = self.client.get(self._detail_url())

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['machine'], self.machine.pk)
        self.assertEqual(data['estimated_minutes'], 240)
        self.assertEqual(data['work_order_type'], WorkOrderType.PREVENTIVE)
        self.assertIsNotNone(data['scheduled_start'])
        self.assertIsNotNone(data['scheduled_end'])

    def test_typed_work_order_fields_are_readable(self):
        """One card shape across board, calendar and timeline.

        ``lifecycle_version`` in particular is published because Phase 3 uses it
        as the ``expected_version`` optimistic-concurrency token.
        """
        data = self.client.get(self._detail_url()).json()

        self.assertEqual(data['lifecycle_status'], WorkOrderLifecycle.DRAFT)
        self.assertEqual(data['lifecycle_version'], 1)
        self.assertIn('reference', data)
        self.assertIn('actual_started_at', data)

    def test_denormalized_labels_avoid_a_lookup_per_card(self):
        data = self.client.get(self._detail_url()).json()

        self.assertEqual(data['machine_name'], 'Press 7')
        self.assertEqual(data['machine_location'], 'Bay 4')
        self.assertEqual(data['assigned_to_username'], 'sched-sup')
        self.assertEqual(data['assigned_to_name'], 'Ada Lovelace')

    def test_assigned_to_name_falls_back_to_username(self):
        self.user.first_name = ''
        self.user.last_name = ''
        self.user.save(update_fields=['first_name', 'last_name'])

        data = self.client.get(self._detail_url()).json()

        self.assertEqual(data['assigned_to_name'], 'sched-sup')

    def test_labels_are_null_when_relations_are_unset(self):
        bare = WorkOrder.objects.create(
            title='Unassigned', status='backlog', priority='low'
        )

        data = self.client.get(
            reverse('kanban-card-detail', kwargs={'pk': bare.pk})
        ).json()

        self.assertIsNone(data['machine'])
        self.assertIsNone(data['machine_name'])
        self.assertIsNone(data['assigned_to_name'])

    def test_schedule_is_writable(self):
        response = self.client.patch(
            self._detail_url(),
            data={
                'scheduled_start': '2026-08-10T08:00:00Z',
                'scheduled_end': '2026-08-10T16:00:00Z',
                'estimated_minutes': 480,
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.estimated_minutes, 480)
        # Compared field-wise rather than against a fixed instant: this project
        # sets USE_TZ = not TESTING, so stored datetimes are naive under test and
        # aware in production. A direct == against an aware value fails here for
        # reasons that have nothing to do with the code under test.
        self.assertEqual(
            (self.work_order.scheduled_start.year, self.work_order.scheduled_start.month),
            (2026, 8),
        )
        self.assertEqual(self.work_order.scheduled_start.day, 10)
        self.assertEqual(self.work_order.scheduled_start.hour, 8)

    def test_end_before_start_is_rejected(self):
        response = self.client.patch(
            self._detail_url(),
            data={
                'scheduled_start': '2026-08-10T16:00:00Z',
                'scheduled_end': '2026-08-10T08:00:00Z',
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('scheduled_end', response.json())

    def test_partial_update_validates_against_the_stored_endpoint(self):
        """A PATCH moving only the end must still be checked against the start."""
        response = self.client.patch(
            self._detail_url(),
            data={'scheduled_end': '2026-08-02T09:00:00Z'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('scheduled_end', response.json())

    def test_equal_start_and_end_is_allowed(self):
        """A zero-length placement is a valid pin to an instant, not an error."""
        response = self.client.patch(
            self._detail_url(),
            data={
                'scheduled_start': '2026-08-11T08:00:00Z',
                'scheduled_end': '2026-08-11T08:00:00Z',
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)

    def test_clearing_the_schedule_is_allowed(self):
        response = self.client.patch(
            self._detail_url(),
            data={'scheduled_start': None, 'scheduled_end': None},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.work_order.refresh_from_db()
        self.assertIsNone(self.work_order.scheduled_start)

    def test_lifecycle_owned_fields_cannot_be_written(self):
        """Board edits must not drive lifecycle state; commands own it.

        These fields are not in ``fields`` at all, so DRF ignores them silently.
        Asserted explicitly because "ignored" and "applied" look identical in a
        200 response.
        """
        original_status = self.work_order.lifecycle_status
        original_version = self.work_order.lifecycle_version
        original_reference = self.work_order.reference

        response = self.client.patch(
            self._detail_url(),
            data={
                'lifecycle_status': WorkOrderLifecycle.COMPLETED,
                'lifecycle_version': 99,
                'reference': 'WO-HACK',
                'actual_completed_at': '2026-08-01T00:00:00Z',
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, original_status)
        self.assertEqual(self.work_order.lifecycle_version, original_version)
        self.assertIsNone(self.work_order.actual_completed_at)
        # The reference is model-assigned and read-only: a board PATCH may not
        # replace it. (It used to be None here only because nothing assigned one.)
        self.assertEqual(self.work_order.reference, original_reference)
        self.assertNotEqual(self.work_order.reference, 'WO-HACK')

    def test_assigned_to_is_read_only(self):
        """Assignment goes through the canonical command, as in WorkOrderSerializer."""
        other = get_user_model().objects.create_user(
            username='other-tech', email='other@example.com', password='pw'
        )

        self.client.patch(
            self._detail_url(),
            data={'assigned_to': other.pk},
            content_type='application/json',
        )

        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.assigned_to, self.user)


class KanbanWindowFilterTest(TestCase):
    """``min_date`` / ``max_date`` select cards overlapping a viewport."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='win-sup', email='win@example.com', password='pw'
        )
        self.client.force_login(self.user)
        self.url = reverse('kanban-card-list')

        # August 2026 is the viewport under test in most cases below.
        self.inside = self._card(
            'inside', start=_utc(2026, 8, 10), end=_utc(2026, 8, 11)
        )
        self.before = self._card(
            'before', start=_utc(2026, 7, 1), end=_utc(2026, 7, 2)
        )
        self.after = self._card(
            'after', start=_utc(2026, 9, 20), end=_utc(2026, 9, 21)
        )
        # Starts before and ends after the window: overlaps without either
        # endpoint being inside it. A containment test would wrongly drop this.
        self.spanning = self._card(
            'spanning', start=_utc(2026, 7, 20), end=_utc(2026, 9, 5)
        )

    def _card(self, title, *, start=None, end=None, due=None):
        return WorkOrder.objects.create(
            title=title,
            status='backlog',
            priority='low',
            scheduled_start=start,
            scheduled_end=end,
            due_date=due,
        )

    def _titles(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return {row['title'] for row in response.json()}

    def test_window_returns_overlapping_cards_only(self):
        titles = self._titles(min_date='2026-08-01', max_date='2026-08-31')

        self.assertEqual(titles, {'inside', 'spanning'})

    def test_a_card_spanning_the_whole_window_is_included(self):
        """The reason this is overlap and not containment."""
        titles = self._titles(min_date='2026-08-10', max_date='2026-08-12')

        self.assertIn('spanning', titles)

    def test_boundaries_are_inclusive(self):
        starting = self._titles(min_date='2026-08-11', max_date='2026-08-31')
        ending = self._titles(min_date='2026-08-01', max_date='2026-08-10')

        self.assertIn('inside', starting)
        self.assertIn('inside', ending)

    def test_unscheduled_cards_fall_back_to_due_date(self):
        self._card('due-inside', due=date(2026, 8, 15))
        self._card('due-outside', due=date(2026, 12, 25))

        titles = self._titles(min_date='2026-08-01', max_date='2026-08-31')

        self.assertIn('due-inside', titles)
        self.assertNotIn('due-outside', titles)

    def test_a_schedule_takes_precedence_over_the_due_date(self):
        """A scheduled card is placed by its schedule, not by when it is due."""
        self._card(
            'scheduled-elsewhere',
            start=_utc(2026, 12, 1),
            end=_utc(2026, 12, 2),
            due=date(2026, 8, 15),
        )

        titles = self._titles(min_date='2026-08-01', max_date='2026-08-31')

        self.assertNotIn('scheduled-elsewhere', titles)

    def test_cards_with_no_dates_are_excluded_from_a_window(self):
        """Undated work has no position in time; the board still shows it."""
        self._card('no-dates')

        windowed = self._titles(min_date='2026-08-01', max_date='2026-08-31')
        unwindowed = self._titles()

        self.assertNotIn('no-dates', windowed)
        self.assertIn('no-dates', unwindowed)

    def test_a_start_only_card_registers_on_its_start_day(self):
        self._card('start-only', start=_utc(2026, 8, 14))

        self.assertIn(
            'start-only', self._titles(min_date='2026-08-01', max_date='2026-08-31')
        )
        self.assertNotIn(
            'start-only', self._titles(min_date='2026-09-01', max_date='2026-09-30')
        )

    def test_an_end_only_card_registers_on_its_end_day(self):
        self._card('end-only', end=_utc(2026, 8, 14))

        self.assertIn(
            'end-only', self._titles(min_date='2026-08-01', max_date='2026-08-31')
        )
        self.assertNotIn(
            'end-only', self._titles(min_date='2026-09-01', max_date='2026-09-30')
        )

    def test_each_bound_works_on_its_own(self):
        from_august = self._titles(min_date='2026-08-01')
        until_august = self._titles(max_date='2026-08-31')

        self.assertNotIn('before', from_august)
        self.assertIn('after', from_august)
        self.assertIn('before', until_august)
        self.assertNotIn('after', until_august)

    def test_no_window_returns_every_active_card(self):
        self.assertEqual(
            self._titles(), {'inside', 'before', 'after', 'spanning'}
        )

    def test_window_composes_with_other_filters(self):
        self._card('other-priority', start=_utc(2026, 8, 12), end=_utc(2026, 8, 13))
        WorkOrder.objects.filter(title='other-priority').update(priority='high')

        titles = self._titles(
            min_date='2026-08-01', max_date='2026-08-31', priority='high'
        )

        self.assertEqual(titles, {'other-priority'})

    def test_archived_cards_stay_excluded_within_a_window(self):
        self.inside.is_active = False
        self.inside.save(update_fields=['is_active'])

        self.assertNotIn(
            'inside', self._titles(min_date='2026-08-01', max_date='2026-08-31')
        )

    def test_response_is_not_paginated(self):
        """The board reads response.data directly, with no results envelope."""
        response = self.client.get(self.url, {'min_date': '2026-08-01'})

        self.assertIsInstance(response.json(), list)


class KanbanScheduleQueryCountTest(TestCase):
    """The denormalized labels must not reintroduce an N+1 on an unpaginated list."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='count-sup', email='count@example.com', password='pw'
        )
        self.client.force_login(self.user)

    def _build(self, count):
        WorkOrder.objects.all().delete()
        customer = Company.objects.create(
            name=f'Count Cust {count}', is_customer=True
        )
        for index in range(count):
            machine = AssetMachine.objects.create(
                name=f'M{count}-{index}', customer=customer
            )
            WorkOrder.objects.create(
                title=f'card-{index}',
                status='backlog',
                priority='low',
                machine=machine,
                assigned_to=self.user,
                scheduled_start=_utc(2026, 8, 3) + timedelta(days=index),
            )

    def _query_count(self, url):
        # Warm up first: the settings cache lazily INSERTs rows on the first
        # request of a test, which would otherwise be counted against whichever
        # measurement happened to run first.
        self.client.get(url)

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
        return len(captured)

    def test_query_count_is_independent_of_card_count(self):
        """Adding cards must not add queries.

        Covers three relations that would each otherwise be one query per card on
        an unpaginated list: ``machine`` and ``assigned_to`` (select_related, for
        the labels this slice adds) and ``work_order_parts`` (prefetch_related, which was
        already N+1 before this change).

        Asserted as a comparison rather than a fixed number so unrelated
        middleware or auth queries do not make this brittle.
        """
        url = reverse('kanban-card-list')

        self._build(1)
        one_card = self._query_count(url)

        self._build(10)
        ten_cards = self._query_count(url)

        self.assertEqual(
            one_card,
            ten_cards,
            'query count grew with card count: one of machine, assigned_to or '
            'work_order_parts is not being select_related/prefetch_related, so the '
            'unpaginated list issues an N+1',
        )
