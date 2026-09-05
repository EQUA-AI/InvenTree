"""M1 PR F (§9.8): the monthly aggregate keeps the builder's sections after the scrub."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from aichat.models import AIUsageMonthlyAggregate, ChatMessage, ChatThread
from aichat.services import retention


class ContextSectionsAggregateTest(TestCase):
    """context_sections rows land in the same transaction as turn_usage rows."""

    def setUp(self):
        """One owner, two usage-bearing turns fourteen months back."""
        self.user = get_user_model().objects.create_user(username='sections-retention')
        self.thread = ChatThread.objects.create(
            owner=self.user, scope_key='k', scope_hash='h', namespace='unscoped'
        )
        self.month = (timezone.now() - timedelta(days=430)).date().replace(day=1)
        builder = {
            'source': 'context_builder',
            'recent_turns': 3,
            'summary_present': 1,
            'section_tokens': 250,
            'wall_ms': 30,
            'degraded': 0,
        }
        for sequence in (1, 2):
            message = ChatMessage.objects.create(
                thread=self.thread,
                sequence=sequence,
                role='assistant',
                content='a',
                metadata={
                    'usage': {
                        'totals': {
                            'input_tokens': 100,
                            'output_tokens': 10,
                            'total_tokens': 110,
                        },
                        'events': [
                            {
                                'source': 'wf8_lookup',
                                'input_tokens': 100,
                                'output_tokens': 10,
                                'total_tokens': 110,
                            },
                            builder
                            if sequence == 1
                            else {**builder, 'summary_present': 0},
                        ],
                    }
                },
            )
            ChatMessage.objects.filter(pk=message.pk).update(
                created_at=timezone.now() - timedelta(days=430)
            )

    def test_aggregate_writes_section_rows_beside_turn_usage(self):
        """Dimension = section, turn_count = turns carrying it, total_tokens = estimate."""
        retention._aggregate_usage_month(
            self.month,
            ChatMessage.objects.filter(thread=self.thread, metadata__has_key='usage'),
        )
        rows = {
            (row.source, row.dimension): row
            for row in AIUsageMonthlyAggregate.objects.filter(
                month=self.month, user_id=self.user.pk
            )
        }
        self.assertEqual(rows[('turn_usage', '')].turn_count, 2)
        recent = rows[('context_sections', 'recent_turns')]
        self.assertEqual(recent.turn_count, 2)
        self.assertEqual(recent.total_tokens, 500)
        summary = rows[('context_sections', 'thread_summary')]
        self.assertEqual(summary.turn_count, 1)
        self.assertEqual(summary.total_tokens, 0)
        # Nothing content-bearing lands in the aggregate table.
        self.assertFalse(
            AIUsageMonthlyAggregate.objects.filter(dimension__icontains='seal').exists()
        )
