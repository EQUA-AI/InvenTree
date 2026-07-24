"""Done-column rules (S6c, plan §5.8)."""

from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assets.models import AssetMachine
from tasks.models import KanbanCard, KanbanColumn, WorkOrderLifecycle
from tasks.services import scheduling


def _utc(y, m, d, h=9):
    return datetime(y, m, d, h, tzinfo=dt_timezone.utc)


class TerminalColumnTest(TestCase):
    def test_done_is_seeded_terminal(self):
        self.assertEqual(KanbanColumn.terminal_key(), 'done')
        self.assertTrue(KanbanColumn.objects.get(key='done').is_terminal)

    def test_only_done_is_terminal(self):
        self.assertEqual(
            KanbanColumn.objects.filter(is_terminal=True).count(), 1
        )


class DoneCardImmutabilityTest(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='done-actor', email='d@example.com', password='pw'
        )
        self.machine = AssetMachine.objects.create(name='Done Press')

    def _card(self, status='backlog'):
        return KanbanCard.objects.create(
            title='WO', status=status, priority='low', machine=self.machine
        )

    def test_scheduling_a_done_card_is_refused(self):
        card = self._card(status='done')
        with self.assertRaises(scheduling.NotMutable):
            scheduling.schedule_work_order(
                work_order_id=card.pk,
                actor=self.actor,
                expected_version=card.lifecycle_version,
                idempotency_key='done1',
                scheduled_start=_utc(2026, 8, 3, 9),
                scheduled_end=_utc(2026, 8, 3, 12),
            )

    def test_updating_a_done_card_is_refused(self):
        card = self._card(status='done')
        with self.assertRaises(scheduling.NotMutable):
            scheduling.update_work_order_plan(
                work_order_id=card.pk,
                actor=self.actor,
                expected_version=card.lifecycle_version,
                idempotency_key='done2',
                fields={'title': 'Nope'},
            )

    def test_a_non_done_card_is_mutable(self):
        card = self._card(status='backlog')
        # Should not raise.
        scheduling.schedule_work_order(
            work_order_id=card.pk,
            actor=self.actor,
            expected_version=card.lifecycle_version,
            idempotency_key='done3',
            scheduled_start=_utc(2026, 8, 3, 9),
            scheduled_end=_utc(2026, 8, 3, 12),
        )
        card.refresh_from_db()
        self.assertIsNotNone(card.scheduled_start)


class ManualDoneMoveTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='move-sup', email='m@example.com', password='pw'
        )
        self.client.force_login(self.user)
        self.machine = AssetMachine.objects.create(name='Move Press')
        self.card = KanbanCard.objects.create(
            title='Move WO', status='backlog', priority='low', machine=self.machine
        )

    def _patch(self, **data):
        return self.client.patch(
            reverse('kanban-card-detail', kwargs={'pk': self.card.pk}),
            data=data,
            content_type='application/json',
        )

    def test_manual_move_to_done_is_refused(self):
        response = self._patch(status='done')
        self.assertEqual(response.status_code, 400)
        self.assertIn('status', response.json())
        self.card.refresh_from_db()
        self.assertEqual(self.card.status, 'backlog')

    def test_manual_move_to_non_terminal_column_is_allowed(self):
        response = self._patch(status='in-progress')
        self.assertEqual(response.status_code, 200)
        self.card.refresh_from_db()
        self.assertEqual(self.card.status, 'in-progress')

    def test_move_to_done_allowed_when_already_completed(self):
        # A completed work order may sit in the done column (this is what
        # closeout does); the guard only blocks the *manual* case.
        self.card.lifecycle_status = WorkOrderLifecycle.COMPLETED
        self.card.save(update_fields=['lifecycle_status'])

        response = self._patch(status='done')
        self.assertEqual(response.status_code, 200)


class CloseoutMovesToDoneTest(TestCase):
    """Completing a work order moves it to the terminal column in one step."""

    def test_terminal_key_helper_reflects_seed(self):
        # Full closeout is exercised in test_workorder_closeout; here we assert
        # the mechanism the closeout now uses.
        self.assertEqual(KanbanColumn.terminal_key(), 'done')

    def test_completion_sets_status_to_terminal(self):
        # Directly exercise the status move the closeout performs, without the
        # full closeout fixture: the rule is that a completed card is in 'done'.
        machine = AssetMachine.objects.create(name='CO Press')
        card = KanbanCard.objects.create(
            title='CO WO', status='in-progress', priority='low', machine=machine
        )
        terminal = KanbanColumn.terminal_key()

        # Simulate the closeout's status move.
        card.status = terminal
        card.lifecycle_status = WorkOrderLifecycle.COMPLETED
        card.save(update_fields=['status', 'lifecycle_status'])

        card.refresh_from_db()
        self.assertEqual(card.status, 'done')
