"""M1 PR F (§9.8): the usage report and pilot metrics fold the builder's sections."""

import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from aichat.models import ChatMessage, ChatThread


def _usage(
    *, builder: dict | None, provider_input: int, cache_write: int | None = None
):
    event = {
        'source': 'wf8_lookup',
        'input_tokens': provider_input,
        'output_tokens': 20,
        'cached_input_tokens': 0,
        'total_tokens': provider_input + 20,
    }
    totals = {
        'input_tokens': provider_input,
        'output_tokens': 20,
        'cached_input_tokens': 0,
        'total_tokens': provider_input + 20,
    }
    if cache_write is not None:
        event['cache_write_tokens'] = cache_write
        totals['cache_write_tokens'] = cache_write
    events = [event]
    if builder is not None:
        events.append({'source': 'context_builder', **builder})
    return {'totals': totals, 'events': events}


class UsageReportSectionsTest(TestCase):
    """Sections, estimator error and the cache-write ratio."""

    @classmethod
    def setUpTestData(cls):
        """Two turns with builder events (one estimator sample), one without."""
        cls.user = get_user_model().objects.create_user(username='sections-user')
        cls.thread = ChatThread.objects.create(
            owner=cls.user, scope_key='k', scope_hash='h', namespace='unscoped'
        )
        builder_a = {
            'history_messages': 5,
            'history_chars': 900,
            'recent_turns': 4,
            'summary_present': 1,
            'dropped_turns': 2,
            'section_tokens': 300,
            'wall_ms': 40,
            'degraded': 0,
            'history_token_estimate': 1100,
        }
        builder_b = {
            'history_messages': 2,
            'history_chars': 200,
            'recent_turns': 2,
            'summary_present': 0,
            'dropped_turns': 0,
            'section_tokens': 60,
            'wall_ms': 20,
            'degraded': 1,
        }
        for sequence, usage in enumerate(
            (
                _usage(builder=builder_a, provider_input=1000, cache_write=1024),
                _usage(builder=builder_b, provider_input=500),
                _usage(builder=None, provider_input=300),
            ),
            start=1,
        ):
            ChatMessage.objects.create(
                thread=cls.thread,
                sequence=sequence,
                role='assistant',
                content='a',
                metadata={'usage': usage},
            )

    def test_json_report_carries_sections_estimator_and_write_ratio(self):
        """Counts only; the estimator error is |estimate-actual|/actual."""
        out = StringIO()
        call_command('usage_report', '--days', '2', '--json', stdout=out)
        report = json.loads(out.getvalue())
        self.assertEqual(report['turns_with_usage'], 3)
        sections = report['sections']
        self.assertEqual(sections['turns'], 2)
        self.assertEqual(sections['recent_turns'], 6)
        self.assertEqual(sections['summary_present'], 1)
        self.assertEqual(sections['dropped_turns'], 2)
        self.assertEqual(sections['section_tokens'], 360)
        self.assertEqual(sections['degraded'], 1)
        self.assertEqual(sections['wall_ms_mean'], 30.0)
        self.assertEqual(report['estimator_error']['samples'], 1)
        self.assertAlmostEqual(
            report['estimator_error']['mean_abs_relative'], 0.1, places=4
        )
        sources = {entry['source']: entry for entry in report['per_source']}
        self.assertEqual(sources['wf8_lookup']['cache_write_tokens'], 1024)
        self.assertAlmostEqual(
            sources['wf8_lookup']['cache_write_ratio'], 1024 / 1800, places=4
        )
        # The builder's own event never counts as provider tokens.
        self.assertNotIn('context_builder', sources)
        self.assertNotIn('seal worn', out.getvalue())

    def test_pilot_metrics_usage_stats_carry_the_same_sections(self):
        """The pilot ops report reads the same block."""
        from aichat.reports.pilot_metrics import usage_stats

        stats = usage_stats(timezone.now() - timezone.timedelta(days=2))
        self.assertEqual(stats['turns_with_usage'], 3)
        self.assertEqual(stats['sections']['turns'], 2)
        self.assertEqual(stats['sections']['section_tokens'], 360)
        self.assertEqual(stats['totals']['cache_write_tokens'], 1024)
