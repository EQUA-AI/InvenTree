"""S37: usage_report command aggregates persisted turn usage metadata."""

import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from aichat.models import ChatMessage, ChatThread


class UsageReportCommandTest(TestCase):
    """Read-only aggregation over ChatMessage.metadata['usage']."""

    @classmethod
    def setUpTestData(cls):
        """One thread with two usage-bearing assistant messages."""
        cls.user = get_user_model().objects.create_user(username='usage-user')
        cls.thread = ChatThread.objects.create(
            owner=cls.user, scope_key='k', scope_hash='h', namespace='unscoped'
        )
        ChatMessage.objects.create(
            thread=cls.thread,
            sequence=1,
            role='assistant',
            content='a',
            metadata={
                'usage': {
                    'totals': {
                        'input_tokens': 100,
                        'output_tokens': 20,
                        'cached_input_tokens': 40,
                        'total_tokens': 120,
                    },
                    'events': [
                        {
                            'source': 'wf8_lookup',
                            'input_tokens': 100,
                            'output_tokens': 20,
                            'cached_input_tokens': 40,
                            'total_tokens': 120,
                        }
                    ],
                }
            },
        )
        ChatMessage.objects.create(
            thread=cls.thread,
            sequence=2,
            role='assistant',
            content='b',
            metadata={
                'usage': {
                    'totals': {'input_tokens': 50, 'output_tokens': 5, 'total_tokens': 55},
                    'events': [
                        {
                            'source': 'luna_diagnostics',
                            'input_tokens': 50,
                            'output_tokens': 5,
                            'total_tokens': 55,
                        }
                    ],
                }
            },
        )
        # A message without usage metadata must be ignored.
        ChatMessage.objects.create(
            thread=cls.thread, sequence=3, role='user', content='c', metadata={}
        )

    def test_json_report_totals_and_hit_rate(self):
        """Canonical totals sum per user/day; hit rate = cached/input."""
        out = StringIO()
        call_command('usage_report', '--days', '2', '--json', stdout=out)
        report = json.loads(out.getvalue())

        self.assertEqual(report['turns_with_usage'], 2)
        self.assertEqual(len(report['per_user_day']), 1)
        row = report['per_user_day'][0]
        self.assertEqual(row['user'], 'usage-user')
        self.assertEqual(row['input_tokens'], 150)
        self.assertEqual(row['output_tokens'], 25)
        self.assertEqual(row['total_tokens'], 175)
        self.assertEqual(row['cached_input_tokens'], 40)
        self.assertAlmostEqual(row['cached_hit_rate'], 40 / 150, places=4)

        sources = {entry['source']: entry for entry in report['per_source']}
        self.assertEqual(sources['wf8_lookup']['cached_input_tokens'], 40)
        self.assertIsNone(sources['luna_diagnostics']['cached_hit_rate'] or None)

    def test_human_output_runs(self):
        out = StringIO()
        call_command('usage_report', stdout=out)
        self.assertIn('turns with usage', out.getvalue())
