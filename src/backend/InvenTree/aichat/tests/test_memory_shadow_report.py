"""Authored report-boundary and unknown-evidence regressions."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from aichat.models import AIWorkerUsageEvent, MemoryExtractionRun
from aichat.services.memory_shadow_report import report_window


class MemoryShadowReportTests(TestCase):
    """No sample, provider uncertainty or dropped detail becomes acceptance."""

    def test_empty_window_preserves_unknown_quality_gates(self):
        """Empty observations do not imply perfect extraction or zero leaks."""
        now = timezone.now()
        result = report_window(since=now - timedelta(days=1), until=now)
        self.assertEqual(result['runs'], 0)
        self.assertIsNone(result['observed_complete_fraction'])
        self.assertIsNone(result['latency']['p95_ms'])
        self.assertEqual(result['decision'], 'not_qualified')
        self.assertTrue(
            all(row['value'] is None for row in result['memory_gate_results'].values())
        )

    def test_closed_window_excludes_newer_runs_and_labels_reserved_spend(self):
        """Upper bounds cannot silently become measured provider token usage."""
        older = timezone.now() - timedelta(hours=1)
        included = MemoryExtractionRun.objects.create(
            owner_id=123, outcome='complete', n_proposals=2, latency_ms=100
        )
        MemoryExtractionRun.objects.filter(pk=included.pk).update(created_at=older)
        MemoryExtractionRun.objects.create(owner_id=999, outcome='failed')
        event = AIWorkerUsageEvent.objects.create(
            purpose='extraction', task='memory_extraction_reserved', input_tokens=200
        )
        AIWorkerUsageEvent.objects.filter(pk=event.pk).update(created_at=older)
        result = report_window(
            since=older - timedelta(hours=1), until=older + timedelta(minutes=1)
        )
        self.assertEqual(result['runs'], 1)
        self.assertEqual(result['counts']['n_proposals'], 2)
        self.assertTrue(result['worker_usage'][0]['estimated_upper_bound'])
        self.assertNotIn('owner_id', str(result))
