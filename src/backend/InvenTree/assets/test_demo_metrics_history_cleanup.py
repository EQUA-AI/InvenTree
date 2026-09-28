"""Provenance reconciliation between history import and cleanup (work G/H).

The history ledger seed must live in the same fingerprint space that
``cleanup.object_fingerprint`` re-reads later: hashes of the persisted,
session-anchor-shifted interval rows. A seed derived from the raw fixture
event instead would make every clean, untouched interval look like an
operator edit, and cleanup would refuse to delete its own pristine rows.
"""

import datetime as dt

from django.test import TestCase

from aimms_testing import requires_postgres
from assets.demo_metrics import cleanup
from assets.demo_metrics.demo_test_support import DemoMetricsEnvMixin
from assets.demo_metrics_models import (
    DemoMetricsCoverageInterval,
    DemoMetricsDowntimeInterval,
    DemoMetricsObject,
)

HISTORY_KINDS = (
    DemoMetricsObject.Kind.COVERAGE_INTERVAL,
    DemoMetricsObject.Kind.DOWNTIME_INTERVAL,
)


@requires_postgres
class HistorySeedProvenanceTest(DemoMetricsEnvMixin, TestCase):
    """Governed history import seeds fingerprints in the cleanup space."""

    def setUp(self):
        """Apply a governed session including the approved history import."""
        self.build_demo_env(history_import_approved=True)
        self.apply_demo(include_history=True)
        self.session = self.session()

    def test_history_rows_seed_in_the_cleanup_fingerprint_space(self):
        """No drift between seeded and recomputed fingerprints after import."""
        rows = list(self.session.ledger_objects.filter(kind__in=HISTORY_KINDS))
        self.assertEqual(len(rows), 41)
        for row in rows:
            self.assertEqual(row.seed_fingerprint, row.last_fingerprint)
            self.assertEqual(
                row.seed_fingerprint,
                cleanup.object_fingerprint(row),
                f'ledger row {row.fixture_key} seeds outside the cleanup space',
            )

    def test_cleanup_deletes_pristine_history_and_preserves_edited_intervals(self):
        """Delete untouched intervals; retain independently modified ones."""
        coverage_total = DemoMetricsCoverageInterval.objects.count()
        downtime_total = DemoMetricsDowntimeInterval.objects.count()
        self.assertEqual(coverage_total, 28)
        self.assertEqual(downtime_total, 13)

        # Independently modify one coverage and one downtime interval.
        edited_coverage = DemoMetricsCoverageInterval.objects.order_by(
            'event_key'
        ).first()
        edited_coverage.end_at = edited_coverage.end_at + dt.timedelta(hours=1)
        edited_coverage.save(update_fields=['end_at'])
        edited_downtime = DemoMetricsDowntimeInterval.objects.order_by(
            'event_key'
        ).first()
        edited_downtime.start_at = edited_downtime.start_at + dt.timedelta(minutes=5)
        edited_downtime.save(update_fields=['start_at'])

        plan = cleanup.build_cleanup_plan(self.session, self.actor)
        deletions = {(item['kind'], item['key']) for item in plan['deletions']}
        retained = {(item['kind'], item['key']) for item in plan['retained_modified']}
        self.assertIn(('coverage_interval', edited_coverage.event_key), retained)
        self.assertIn(('downtime_interval', edited_downtime.event_key), retained)
        self.assertNotIn(('coverage_interval', edited_coverage.event_key), deletions)
        self.assertNotIn(('downtime_interval', edited_downtime.event_key), deletions)
        self.assertEqual(
            sum(1 for item in plan['deletions'] if item['kind'] == 'coverage_interval'),
            coverage_total - 1,
        )
        self.assertEqual(
            sum(1 for item in plan['deletions'] if item['kind'] == 'downtime_interval'),
            downtime_total - 1,
        )

        result = cleanup.apply_cleanup(
            self.session, self.actor, approved_cleanup_sha256=plan['plan_hash']
        )
        self.assertEqual(result['deleted']['coverage_interval'], coverage_total - 1)
        self.assertEqual(result['deleted']['downtime_interval'], downtime_total - 1)
        retained_after = {
            (item['kind'], item['key']) for item in result['retained_modified']
        }
        self.assertIn(('coverage_interval', edited_coverage.event_key), retained_after)
        self.assertIn(('downtime_interval', edited_downtime.event_key), retained_after)
        self.assertEqual(DemoMetricsCoverageInterval.objects.count(), 1)
        self.assertEqual(DemoMetricsDowntimeInterval.objects.count(), 1)
        self.assertTrue(
            DemoMetricsCoverageInterval.objects.filter(pk=edited_coverage.pk).exists()
        )
        self.assertTrue(
            DemoMetricsDowntimeInterval.objects.filter(pk=edited_downtime.pk).exists()
        )


class IntervalFingerprintTimeConsistencyTest(TestCase):
    """The interval fingerprint schema is stable across naive/zero-offset."""

    @staticmethod
    def _coverage(start):
        """Build one unsaved coverage interval starting at ``start``."""
        return DemoMetricsCoverageInterval(
            session_id=1,
            event_key='coverage/A01/00',
            machine_id=7,
            start_at=start,
            end_at=start + dt.timedelta(hours=8),
            fully_observed=True,
            planned=True,
            location_alias='A-LINE',
            attribution_mode='synthetic_scenario',
        )

    @staticmethod
    def _downtime(start):
        """Build one unsaved downtime interval starting at ``start``."""
        return DemoMetricsDowntimeInterval(
            session_id=1,
            event_key='downtime/A01/00',
            machine_id=7,
            start_at=start,
            end_at=start + dt.timedelta(minutes=20),
            loss_category='unplanned_loss_of_function',
            failure_key='failure/A01/00',
            coverage_id=None,
            location_alias='A-LINE',
            attribution_mode='synthetic_scenario',
        )

    def test_naive_and_zero_offset_instants_hash_identically(self):
        """A naive instant and its zero-offset form are the same fingerprint."""
        naive = dt.datetime(2026, 9, 10, 8, 0)
        zero_offset = naive.replace(tzinfo=dt.timezone.utc)
        self.assertEqual(
            cleanup.coverage_interval_fingerprint(self._coverage(naive)),
            cleanup.coverage_interval_fingerprint(self._coverage(zero_offset)),
        )
        self.assertEqual(
            cleanup.downtime_interval_fingerprint(self._downtime(naive)),
            cleanup.downtime_interval_fingerprint(self._downtime(zero_offset)),
        )

    def test_aware_offsets_of_the_same_instant_hash_identically(self):
        """Non-UTC aware values hash as their UTC instant, never shifted."""
        utc = dt.datetime(2026, 9, 10, 8, 0, tzinfo=dt.timezone.utc)
        elsewhere = utc.astimezone(dt.timezone(dt.timedelta(hours=2)))
        self.assertEqual(
            cleanup.coverage_interval_fingerprint(self._coverage(utc)),
            cleanup.coverage_interval_fingerprint(self._coverage(elsewhere)),
        )
        self.assertEqual(
            cleanup.downtime_interval_fingerprint(self._downtime(utc)),
            cleanup.downtime_interval_fingerprint(self._downtime(elsewhere)),
        )
