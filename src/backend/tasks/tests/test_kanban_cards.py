"""A work order is the job; a kanban card is a tracked piece of it.

These pin the distinction the model now makes: one work order per maintenance
job, one or more cards for the work of it, and a card that cannot quietly
become a second work order.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from tasks.models import KanbanCard, KanbanColumn, WorkOrder


class PrimaryCardTest(TestCase):
    """Every job gets exactly one card to start with, however it was made."""

    def setUp(self):
        """Create an actor and a work order."""
        suffix = uuid.uuid4().hex[:6]
        self.actor = get_user_model().objects.create_user(
            username=f'card-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.work_order = WorkOrder.objects.create(
            title='Rebuild Pump 2',
            description='Seal and wear ring',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_HIGH,
            assigned_to=self.actor,
        )

    def test_creating_a_work_order_creates_its_card(self):
        """The board renders cards, so a job with none would be invisible."""
        cards = list(self.work_order.cards.all())

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].card_kind, KanbanCard.KIND_WORK_ORDER)
        self.assertEqual(cards[0].title, 'Rebuild Pump 2')
        self.assertEqual(cards[0].status, WorkOrder.STATUS_BACKLOG)

    def test_the_primary_card_is_the_one_tracking_the_job(self):
        """``primary_card`` answers "the card" when nobody said which."""
        self.work_order.cards.create(
            card_kind=KanbanCard.KIND_PROCUREMENT,
            status=WorkOrder.STATUS_BACKLOG,
            title='Source the seal kit',
        )

        primary = self.work_order.primary_card

        self.assertIsNotNone(primary)
        self.assertEqual(primary.card_kind, KanbanCard.KIND_WORK_ORDER)
        self.assertEqual(primary.title, 'Rebuild Pump 2')

    def test_saving_an_existing_work_order_adds_no_card(self):
        """Only creation makes a card; an edit is not a new piece of work."""
        self.work_order.title = 'Rebuild Pump 2 (revised)'
        self.work_order.save()

        self.assertEqual(self.work_order.cards.count(), 1)

    def test_one_job_can_hold_several_cards_at_once(self):
        """The whole point: one work order, several columns simultaneously."""
        self.work_order.cards.create(
            card_kind=KanbanCard.KIND_SUBTASK,
            status=WorkOrder.STATUS_IN_PROGRESS,
            title='Diagnose',
        )
        self.work_order.cards.create(
            card_kind=KanbanCard.KIND_PROCUREMENT,
            status=WorkOrder.STATUS_REVIEW,
            title='Source the seal kit',
        )

        self.assertEqual(self.work_order.cards.count(), 3)
        self.assertEqual(
            {card.status for card in self.work_order.cards.all()},
            {
                WorkOrder.STATUS_BACKLOG,
                WorkOrder.STATUS_IN_PROGRESS,
                WorkOrder.STATUS_REVIEW,
            },
        )
        # Still one job. That is the invariant the split exists to protect.
        self.assertEqual(WorkOrder.objects.filter(pk=self.work_order.pk).count(), 1)

    def test_deleting_the_job_takes_its_cards(self):
        """A card has no meaning without the work order it tracks."""
        card_ids = list(self.work_order.cards.values_list('pk', flat=True))
        self.work_order.delete()

        self.assertFalse(KanbanCard.objects.filter(pk__in=card_ids).exists())


class CardDelegationTest(TestCase):
    """A card answers for itself, or defers to the job."""

    def setUp(self):
        """Create a scheduled, assigned work order and a bare extra card."""
        suffix = uuid.uuid4().hex[:6]
        self.owner = get_user_model().objects.create_user(
            username=f'owner-{suffix}', email=f'o{suffix}@example.com', password='pw'
        )
        self.fitter = get_user_model().objects.create_user(
            username=f'fitter-{suffix}', email=f'f{suffix}@example.com', password='pw'
        )
        self.start = timezone.now()
        self.work_order = WorkOrder.objects.create(
            title='Rebuild Pump 2',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_HIGH,
            assigned_to=self.owner,
            scheduled_start=self.start,
            scheduled_end=self.start + timezone.timedelta(hours=6),
        )

    def test_an_unset_card_defers_to_the_job(self):
        """Unset means "whatever the job says", not "nobody" and "never"."""
        card = self.work_order.cards.create(
            card_kind=KanbanCard.KIND_SUBTASK,
            status=WorkOrder.STATUS_BACKLOG,
            title='Diagnose',
        )

        self.assertEqual(card.effective_assignee, self.owner)
        self.assertEqual(card.effective_start, self.work_order.scheduled_start)
        self.assertEqual(card.effective_end, self.work_order.scheduled_end)

    def test_a_card_that_diverges_keeps_its_own_answer(self):
        """Only a piece that genuinely differs carries its own values."""
        own_start = self.start + timezone.timedelta(days=1)
        card = self.work_order.cards.create(
            card_kind=KanbanCard.KIND_PROCUREMENT,
            status=WorkOrder.STATUS_REVIEW,
            title='Source the seal kit',
            assigned_to=self.fitter,
            scheduled_start=own_start,
        )

        self.assertEqual(card.effective_assignee, self.fitter)
        self.assertEqual(card.effective_start, own_start)
        # Unset end still defers, independently of the start.
        self.assertEqual(card.effective_end, self.work_order.scheduled_end)


class ColumnOccupancyTest(TestCase):
    """A column counts cards, because cards are what sit in it."""

    def setUp(self):
        """Create a column and a work order in it."""
        self.column = KanbanColumn.objects.create(
            key=f'stage-{uuid.uuid4().hex[:6]}', label='Stage', order=99
        )
        self.work_order = WorkOrder.objects.create(
            title='Rebuild Pump 2',
            status=self.column.key,
            priority=WorkOrder.PRIORITY_LOW,
        )

    def test_a_job_broken_into_pieces_occupies_the_column_more_than_once(self):
        """Counting work orders would under-report what deletion strands."""
        self.work_order.cards.create(
            card_kind=KanbanCard.KIND_SUBTASK,
            status=self.column.key,
            title='Second piece',
        )

        self.assertEqual(self.column.card_count(), 2)

    def test_archived_cards_are_not_counted(self):
        """An archived card is not occupying the board."""
        self.work_order.cards.update(is_active=False)

        self.assertEqual(self.column.card_count(), 0)
        self.assertEqual(self.column.card_count(active_only=False), 1)


class BackfillMigrationTest(TestCase):
    """The backfill gives pre-existing jobs the card they never had.

    Work orders created before the split have no card, and ``save()`` only
    makes one on insert - so an upgraded deployment depends entirely on this
    migration to have a board at all. Exercised against real rows written
    around ``save()``, which is the state the migration actually finds.
    """

    def _run_backfill(self):
        import importlib

        from django.apps import apps

        module = importlib.import_module(
            'tasks.migrations.0025_backfill_primary_cards'
        )
        module.create_primary_cards(apps, None)

    def _work_order_without_a_card(self, **overrides):
        """Insert a work order the way an upgraded database already holds one."""
        values = {
            'title': 'Pre-existing job',
            'description': '',
            'status': WorkOrder.STATUS_REVIEW,
            'priority': WorkOrder.PRIORITY_MEDIUM,
            'is_active': True,
        }
        values.update(overrides)
        work_order = WorkOrder(**values)
        # bulk_create bypasses save(), so no card is generated - exactly the
        # shape of a row written before this model existed.
        WorkOrder.objects.bulk_create([work_order])
        return WorkOrder.objects.get(pk=work_order.pk)

    def test_a_job_without_a_card_gets_one(self):
        """The board would otherwise be empty after upgrading."""
        work_order = self._work_order_without_a_card()
        self.assertEqual(work_order.cards.count(), 0)

        self._run_backfill()

        [card] = work_order.cards.all()
        self.assertEqual(card.card_kind, KanbanCard.KIND_WORK_ORDER)
        self.assertEqual(card.status, WorkOrder.STATUS_REVIEW)
        self.assertEqual(card.title, 'Pre-existing job')

    def test_the_card_carries_the_job_board_state_over(self):
        """Position, assignment and window survive the move."""
        user = get_user_model().objects.create_user(
            username=f'bf-{uuid.uuid4().hex[:6]}', password='pw'
        )
        start = timezone.now()
        work_order = self._work_order_without_a_card(
            assigned_to=user,
            assignee='Legacy Name',
            scheduled_start=start,
            scheduled_end=start + timezone.timedelta(hours=2),
            estimated_minutes=120,
        )

        self._run_backfill()

        card = work_order.cards.get()
        self.assertEqual(card.assigned_to, user)
        self.assertEqual(card.assignee, 'Legacy Name')
        self.assertEqual(card.scheduled_start, start)
        self.assertEqual(card.estimated_minutes, 120)
        self.assertEqual(card.card_kind, KanbanCard.KIND_WORK_ORDER)

    def test_rerunning_creates_no_duplicates(self):
        """A partial apply must be safe to repeat."""
        work_order = self._work_order_without_a_card()

        self._run_backfill()
        self._run_backfill()

        self.assertEqual(work_order.cards.count(), 1)

    def test_a_job_that_already_has_a_card_is_left_alone(self):
        """Jobs created after the split already have theirs."""
        work_order = WorkOrder.objects.create(
            title='Already carded',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_LOW,
        )
        original = work_order.cards.get().pk

        self._run_backfill()

        self.assertEqual([c.pk for c in work_order.cards.all()], [original])
