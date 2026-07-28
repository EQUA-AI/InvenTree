"""The board reads and writes cards; the job is changed through its own API.

``/api/kanban/cards/`` used to serve work orders, because a work order and its
card were the same row. It serves cards now, and the work-order surface moved
to ``/api/kanban/work-orders/``. These pin both halves, and the boundary
between them: moving a card is a board edit, not a lifecycle change.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from rest_framework import status
from rest_framework.test import APIClient

from tasks.models import KanbanCard, WorkOrder, WorkOrderLifecycle


class KanbanCardEndpointTest(TestCase):
    """The card collection at /api/kanban/cards/."""

    list_url = '/api/kanban/cards/'

    def setUp(self):
        """Authenticate a user holding work-order rights."""
        suffix = uuid.uuid4().hex[:6]
        self.actor = get_user_model().objects.create_superuser(
            username=f'board-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.work_order = WorkOrder.objects.create(
            title='Rebuild Pump 2',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_HIGH,
            assigned_to=self.actor,
        )

    def detail_url(self, card):
        """Return the card resource URL."""
        return f'{self.list_url}{card.pk}/'

    def test_list_returns_cards_not_work_orders(self):
        """The path says cards; it must not be serving jobs any more."""
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        [payload] = response.json()
        self.assertEqual(payload['work_order'], self.work_order.pk)
        self.assertEqual(payload['card_kind'], KanbanCard.KIND_WORK_ORDER)
        self.assertEqual(payload['title'], 'Rebuild Pump 2')

    def test_a_card_carries_its_job_context(self):
        """One request renders the board, so the job travels with the card."""
        response = self.client.get(self.list_url)

        [payload] = response.json()
        self.assertEqual(payload['work_order_reference'], self.work_order.reference)
        self.assertEqual(payload['priority'], WorkOrder.PRIORITY_HIGH)
        self.assertEqual(
            payload['lifecycle_status'], self.work_order.lifecycle_status
        )
        self.assertEqual(payload['effective_assignee'], self.actor.pk)

    def test_a_job_can_be_given_another_card(self):
        """Breaking work down is creating a card, not another work order."""
        response = self.client.post(
            self.list_url,
            {
                'work_order': self.work_order.pk,
                'card_kind': KanbanCard.KIND_PROCUREMENT,
                'status': WorkOrder.STATUS_REVIEW,
                'title': 'Source the seal kit',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.work_order.cards.count(), 2)
        self.assertEqual(WorkOrder.objects.count(), 1)

    def test_moving_a_card_does_not_move_the_job(self):
        """A board edit changes a column, never the lifecycle."""
        card = self.work_order.primary_card
        original_lifecycle = self.work_order.lifecycle_status

        response = self.client.patch(
            self.detail_url(card),
            {'status': WorkOrder.STATUS_IN_PROGRESS},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        card.refresh_from_db()
        self.work_order.refresh_from_db()
        self.assertEqual(card.status, WorkOrder.STATUS_IN_PROGRESS)
        self.assertEqual(self.work_order.lifecycle_status, original_lifecycle)
        self.assertNotEqual(
            self.work_order.lifecycle_status, WorkOrderLifecycle.IN_PROGRESS
        )

    def test_a_card_cannot_be_moved_to_another_job(self):
        """Which job a piece of work belongs to is settled at creation.

        Rejected rather than ignored: a silent no-op would look to the board
        like the move had worked.
        """
        other = WorkOrder.objects.create(
            title='Different job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_LOW,
        )
        card = self.work_order.primary_card

        response = self.client.patch(
            self.detail_url(card), {'work_order': other.pk}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        card.refresh_from_db()
        self.assertEqual(card.work_order_id, self.work_order.pk)

    def test_the_jobs_own_card_cannot_be_archived(self):
        """An open job with no card would disappear from the board."""
        response = self.client.delete(self.detail_url(self.work_order.primary_card))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(self.work_order.cards.filter(is_active=True).exists())

    def test_an_extra_card_archives_rather_than_deletes(self):
        """Board history is kept, as it is for work orders."""
        card = self.work_order.cards.create(
            card_kind=KanbanCard.KIND_SUBTASK,
            status=WorkOrder.STATUS_BACKLOG,
            title='Diagnose',
        )

        response = self.client.delete(self.detail_url(card))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        card.refresh_from_db()
        self.assertFalse(card.is_active)
        self.assertTrue(KanbanCard.objects.filter(pk=card.pk).exists())

    def test_cards_can_be_filtered_to_one_job(self):
        """The detail page asks for one job's cards."""
        other = WorkOrder.objects.create(
            title='Different job',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_LOW,
        )

        response = self.client.get(f'{self.list_url}?work_order={other.pk}')

        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['work_order'], other.pk)


class WorkOrderBoardRouteTest(TestCase):
    """The work-order surface moved, and still works."""

    def setUp(self):
        """Authenticate and create one job."""
        suffix = uuid.uuid4().hex[:6]
        self.actor = get_user_model().objects.create_superuser(
            username=f'route-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.work_order = WorkOrder.objects.create(
            title='Rebuild Pump 2',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_HIGH,
        )

    def test_work_orders_are_served_from_their_own_path(self):
        """The route now says what it returns."""
        response = self.client.get('/api/kanban/work-orders/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        [payload] = response.json()
        self.assertEqual(payload['id'], self.work_order.pk)
        self.assertEqual(payload['reference'], self.work_order.reference)

    def test_the_overview_lists_the_jobs_cards(self):
        """The detail page shows the work the job is tracked through."""
        self.work_order.cards.create(
            card_kind=KanbanCard.KIND_PROCUREMENT,
            status=WorkOrder.STATUS_REVIEW,
            title='Source the seal kit',
        )

        response = self.client.get(
            f'/api/kanban/work-orders/{self.work_order.pk}/overview/'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cards = response.json()['cards']
        self.assertEqual(len(cards), 2)
        self.assertEqual(
            {card['title'] for card in cards},
            {'Rebuild Pump 2', 'Source the seal kit'},
        )
