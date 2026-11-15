"""Pure S3 envelope validation; these records are never authority tokens."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
from unittest import TestCase

from assets.integration_contracts import MaintenanceSourceIdentity
from tasks.integration_s3_details import (
    MaintenanceDetailsSourceRecord,
    WorkDetailsSourceRecord,
)
from tasks.integration_sources import WorkSourceIdentity


class DirectDetailsContractTests(TestCase):
    """Validate scalar types without importing an application or business database."""

    def work(self):
        """Use literal synthetic clocks, including a nullable native reference."""
        clock = datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        return WorkDetailsSourceRecord(
            identity=WorkSourceIdentity(environment='s3-test', pk=1),
            reference=None,
            title='Synthetic title',
            description='Sensitive S3 prose',
            status='backlog',
            lifecycle_status='draft',
            is_active=True,
            priority='medium',
            due_date=None,
            work_order_type='corrective',
            created_at=clock,
            updated_at=clock,
            scheduled_start=None,
            scheduled_end=None,
            actual_started_at=None,
            actual_completed_at=None,
            estimated_minutes=None,
            lifecycle_version=1,
            hold_reason='',
            affected_component='',
            affected_component_ref='',
        )

    def maintenance(self):
        """Use a native recorded date, not a guessed midnight timestamp."""
        clock = datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        return MaintenanceDetailsSourceRecord(
            identity=MaintenanceSourceIdentity(environment='s3-test', pk=1),
            date=date(2020, 1, 2),
            summary='Synthetic event',
            details='Sensitive S3 history',
            created_at=clock,
            updated_at=clock,
        )

    def test_wrong_identity_and_scalar_types_are_not_accepted(self):
        """A wrong domain, bool counter or datetime-as-date is not valid source data."""
        work = self.work()
        for change in (
            {'identity': self.maintenance().identity},
            {'lifecycle_version': None},
            {'lifecycle_version': True},
            {'estimated_minutes': -1},
            {'due_date': work.created_at},
            {'is_active': 1},
            {'title': None},
        ):
            with self.subTest(change=tuple(change)):
                with self.assertRaises(ValueError):
                    replace(work, **change)
        maintenance = self.maintenance()
        for change in (
            {'identity': work.identity},
            {'date': work.created_at},
            {'created_at': None},
            {'summary': None},
        ):
            with self.subTest(change=tuple(change)):
                with self.assertRaises(ValueError):
                    replace(maintenance, **change)

    def test_valid_blank_null_zero_and_native_clocks_remain_distinct(self):
        """Do not invent source revision completeness, timezone or nonzero estimates."""
        work = replace(
            self.work(), reference='', estimated_minutes=0, lifecycle_version=0
        )
        self.assertEqual(work.reference, '')
        self.assertEqual(work.estimated_minutes, 0)
        self.assertIsNone(work.actual_completed_at)
        naive = datetime(2020, 1, 2, 3, 4, 5)
        self.assertIsNone(replace(work, created_at=naive).created_at.tzinfo)
        self.assertIs(type(self.maintenance().date), date)
        with self.assertRaises(FrozenInstanceError):
            work.status = 'changed'
        self.assertNotIn('Sensitive S3 prose', repr(work))
        self.assertNotIn('Sensitive S3 history', repr(self.maintenance()))
