"""S37: usage_report command aggregates persisted turn usage metadata."""

import json
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from aichat.models import AIWorkerUsageEvent, ChatMessage, ChatThread


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


class UsageReportWorkerSectionTest(TestCase):
    """M2 §8.4: the worker ledger block by deployment and purpose."""

    @classmethod
    def setUpTestData(cls):
        """Three ledger rows over two deployments and both purposes."""
        cls.user = get_user_model().objects.create_user(username='worker-usage')
        cls.thread = ChatThread.objects.create(
            owner=cls.user, scope_key='k', scope_hash='h', namespace='unscoped'
        )
        AIWorkerUsageEvent.objects.create(
            purpose='summarization',
            task='compact_thread_summary',
            thread=cls.thread,
            deployment='gpt-5-mini-dz',
            input_tokens=1000,
            output_tokens=100,
            attempts=2,
        )
        AIWorkerUsageEvent.objects.create(
            purpose='summarization',
            task='compact_thread_summary',
            thread=cls.thread,
            deployment='standard-4o',
            input_tokens=10,
            output_tokens=1,
        )
        AIWorkerUsageEvent.objects.create(
            purpose='extraction',
            task='extract_memory',
            deployment='gpt-5-mini-dz',
            input_tokens=50,
            output_tokens=5,
        )

    def test_json_worker_section(self):
        out = StringIO()
        call_command('usage_report', '--days', '2', '--json', stdout=out)
        worker = json.loads(out.getvalue())['worker']

        self.assertEqual(worker['rows'], 3)
        self.assertEqual(worker['attempts'], 4)
        self.assertEqual(worker['input_tokens'], 1060)
        self.assertEqual(worker['output_tokens'], 106)
        deployments = {e['deployment']: e for e in worker['per_deployment']}
        self.assertEqual(set(deployments), {'gpt-5-mini-dz', 'standard-4o'})
        self.assertEqual(deployments['gpt-5-mini-dz']['rows'], 2)
        self.assertEqual(deployments['gpt-5-mini-dz']['attempts'], 3)
        self.assertEqual(deployments['gpt-5-mini-dz']['input_tokens'], 1050)
        self.assertEqual(deployments['gpt-5-mini-dz']['output_tokens'], 105)
        self.assertEqual(deployments['standard-4o']['rows'], 1)
        purposes = {e['purpose']: e for e in worker['per_purpose']}
        self.assertEqual(set(purposes), {'summarization', 'extraction'})
        self.assertEqual(purposes['summarization']['rows'], 2)
        self.assertEqual(purposes['summarization']['input_tokens'], 1010)
        self.assertEqual(purposes['extraction']['rows'], 1)
        self.assertNotIn(str(self.thread.pk), out.getvalue())

    def test_window_excludes_old_rows(self):
        old = AIWorkerUsageEvent.objects.create(
            purpose='summarization', deployment='old', input_tokens=999
        )
        AIWorkerUsageEvent.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=3)
        )
        out = StringIO()
        call_command('usage_report', '--days', '2', '--json', stdout=out)
        worker = json.loads(out.getvalue())['worker']
        self.assertEqual(worker['rows'], 3)
        self.assertNotIn('old', {e['deployment'] for e in worker['per_deployment']})

    def test_human_output_has_worker_lines(self):
        out = StringIO()
        call_command('usage_report', stdout=out)
        text = out.getvalue()
        self.assertIn('Worker ledger: 3 rows, 4 calls, in=1060 out=106', text)
        self.assertIn('gpt-5-mini-dz: rows=2 calls=3 in=1050 out=105', text)
        self.assertIn('summarization: rows=2', text)
        self.assertIn('extraction: rows=1', text)
        self.assertNotIn(str(self.thread.pk), text)


class UsageReportEmptyWorkerSectionTest(TestCase):
    """No ledger rows: the section is present with zero totals."""

    def test_empty_section(self):
        out = StringIO()
        call_command('usage_report', '--json', stdout=out)
        worker = json.loads(out.getvalue())['worker']
        self.assertEqual(
            worker,
            {
                'rows': 0,
                'attempts': 0,
                'input_tokens': 0,
                'output_tokens': 0,
                'per_deployment': [],
                'per_purpose': [],
            },
        )
