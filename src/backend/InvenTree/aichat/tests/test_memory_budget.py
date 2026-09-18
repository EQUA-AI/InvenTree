"""PostgreSQL budget reservation cases; no providers or installed workers."""

from types import SimpleNamespace
from unittest import skipUnless

from django.db import connection
from django.test import TestCase

from aichat.models import AIWorkerUsageEvent
from aichat.services import memory_budget as service


@skipUnless(
    connection.vendor == 'postgresql', 'Memory budget uses PostgreSQL advisory locking'
)
class MemoryBudgetTests(TestCase):
    """The ledger counts one reservation per call and fails closed at the cap."""

    def reserve(self):
        """One bounded fixture reservation without a transcript link."""
        return service.reserve(
            tokens=100,
            deployment='fixture',
            thread_id=None,
            settings=SimpleNamespace(aimms_worker_daily_token_cap_extraction=150),
        )

    def test_reserved_capacity_blocks_the_next_call(self):
        """Concurrent admission observes in-flight estimates as already spent."""
        self.assertIsNotNone(self.reserve())
        self.assertIsNone(self.reserve())
        self.assertEqual(AIWorkerUsageEvent.objects.count(), 1)

    def test_unknown_outcome_keeps_the_conservative_reservation(self):
        """A crash or missing usage never silently refunds the daily allowance."""
        identity = self.reserve()
        service.settle(identity, SimpleNamespace(attempted=True, usage_known=False))
        row = AIWorkerUsageEvent.objects.get(pk=identity)
        self.assertEqual(row.input_tokens, 100)
        self.assertEqual(row.task, service.RESERVATION_TASK)

    def test_known_usage_replaces_instead_of_double_counting(self):
        """The actual provider usage edits the original spend row."""
        identity = self.reserve()
        service.settle(
            identity,
            SimpleNamespace(
                attempted=True, usage_known=True, input_tokens=20, output_tokens=10
            ),
        )
        row = AIWorkerUsageEvent.objects.get(pk=identity)
        self.assertEqual(row.input_tokens + row.output_tokens, 30)
        self.assertEqual(row.task, service.COMPLETED_TASK)
        self.assertIsNotNone(self.reserve())
        self.assertEqual(AIWorkerUsageEvent.objects.count(), 2)
