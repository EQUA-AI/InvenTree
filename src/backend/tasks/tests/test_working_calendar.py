"""Tests for the WorkingCalendar model and calendar resolution (S6)."""

from datetime import date, time

from django.core.exceptions import ValidationError
from django.test import TestCase

from assets.models import AssetMachine
from company.models import Company
from tasks.models import WorkOrder, WorkingCalendar
from tasks.services.calendars import calendar_for_card, spec_for_card


class WorkingCalendarModelTest(TestCase):
    def test_default_windows_are_mon_to_fri_nine_to_five(self):
        cal = WorkingCalendar.objects.create(name='Default hours')
        spec = cal.to_spec()

        self.assertEqual(spec.tzname, 'UTC')
        self.assertEqual(spec.windows[0], ((time(9, 0), time(17, 0)),))
        # Saturday (5) and Sunday (6) have no windows.
        self.assertNotIn(5, spec.windows)

    def test_to_spec_parses_split_windows_and_holidays(self):
        cal = WorkingCalendar.objects.create(
            name='Split',
            timezone='America/New_York',
            windows={'0': [['09:00', '12:00'], ['13:00', '17:00']]},
            holidays=['2026-12-25'],
        )
        spec = cal.to_spec()

        self.assertEqual(spec.tzname, 'America/New_York')
        self.assertEqual(
            spec.windows[0],
            ((time(9, 0), time(12, 0)), (time(13, 0), time(17, 0))),
        )
        self.assertIn(date(2026, 12, 25), spec.holidays)

    def test_clean_rejects_unknown_timezone(self):
        cal = WorkingCalendar(name='Bad tz', timezone='Mars/Olympus')
        with self.assertRaises(ValidationError):
            cal.clean()

    def test_clean_rejects_bad_window_shape(self):
        cal = WorkingCalendar(name='Bad win', windows={'0': [['09:00']]})
        with self.assertRaises(ValidationError):
            cal.clean()

    def test_clean_rejects_bad_weekday_key(self):
        cal = WorkingCalendar(name='Bad day', windows={'9': [['09:00', '17:00']]})
        with self.assertRaises(ValidationError):
            cal.clean()


class CalendarResolutionTest(TestCase):
    def setUp(self):
        self.customer = Company.objects.create(name='Cal Cust', is_customer=True)
        self.machine = AssetMachine.objects.create(name='Cal Press')
        self.work_order = WorkOrder.objects.create(
            title='Cal WO', status='backlog', priority='low', machine=self.machine
        )

    def test_machine_calendar_wins(self):
        machine_cal = WorkingCalendar.objects.create(
            name='Machine cal', machine=self.machine, timezone='Europe/Berlin'
        )
        WorkingCalendar.objects.create(
            name='Customer cal', customer=self.customer, timezone='UTC'
        )
        WorkingCalendar.objects.create(
            name='Default cal', is_default=True, timezone='UTC'
        )
        # Even with an explicit customer on the card, the machine calendar wins.
        self.work_order.customer = self.customer
        self.work_order.save()

        self.assertEqual(calendar_for_card(self.work_order), machine_cal)
        self.assertEqual(spec_for_card(self.work_order).tzname, 'Europe/Berlin')

    def test_customer_calendar_used_when_no_machine_calendar(self):
        self.work_order.customer = self.customer
        self.work_order.save()
        customer_cal = WorkingCalendar.objects.create(
            name='Customer cal', customer=self.customer, timezone='Asia/Tokyo'
        )
        WorkingCalendar.objects.create(name='Default cal', is_default=True)

        self.assertEqual(calendar_for_card(self.work_order), customer_cal)

    def test_default_used_when_card_has_no_explicit_customer(self):
        # The card carries no customer of its own, and machines no longer carry
        # a customer identity: a customer-scoped calendar is not considered.
        self.assertIsNone(self.work_order.customer_id)
        WorkingCalendar.objects.create(
            name='Unrelated customer cal', customer=self.customer
        )
        default_cal = WorkingCalendar.objects.create(
            name='Default cal', is_default=True
        )

        self.assertEqual(calendar_for_card(self.work_order), default_cal)

    def test_default_calendar_used_when_nothing_more_specific(self):
        default_cal = WorkingCalendar.objects.create(
            name='Default cal', is_default=True, timezone='UTC'
        )
        self.assertEqual(calendar_for_card(self.work_order), default_cal)

    def test_hardcoded_fallback_when_no_calendars_exist(self):
        # No WorkingCalendar rows at all: spec still resolves so scheduling works.
        self.assertIsNone(calendar_for_card(self.work_order))
        spec = spec_for_card(self.work_order)
        self.assertEqual(spec.tzname, 'UTC')
        self.assertEqual(spec.windows[0], ((time(9, 0), time(17, 0)),))
