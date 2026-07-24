"""API contract tests for canonical maintenance work orders."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from rest_framework import status
from rest_framework.test import APIClient

from company.models import Company
from repair.models import RepairPacket
from tasks.models import (
    KanbanCard,
    WorkOrderCommand,
    WorkOrderEvent,
    WorkOrderLifecycle,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.readiness import (
    ASSET_REQUIRED,
    ASSIGNEE_REQUIRED,
    PACKET_OWNS_LIFECYCLE,
)


@override_settings(AIMMS_WORK_ORDERS_ENABLED=True)
class WorkOrderAPITest(TestCase):
    """Exercise the scoped canonical API without changing the legacy contract."""

    list_url = '/api/tasks/work-orders/'

    def setUp(self):
        """Create an authenticated superuser with one explicit customer scope."""
        self.customer = Company.objects.create(
            name='Canonical API Customer', is_customer=True
        )
        self.other_customer = Company.objects.create(
            name='Out-of-scope API Customer', is_customer=True
        )
        self.actor = get_user_model().objects.create_superuser(
            username='work-order-api-supervisor',
            email='work-order-api@example.com',
            password='test-password',
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.client = APIClient()
        self.client.force_authenticate(self.actor)

    def make_card(self, **overrides):
        """Create a minimally valid, in-scope work order."""
        values = {
            'title': 'Canonical work order',
            'description': 'Typed maintenance work',
            'status': KanbanCard.STATUS_BACKLOG,
            'priority': KanbanCard.PRIORITY_MEDIUM,
            'customer': self.customer,
        }
        values.update(overrides)
        return KanbanCard.objects.create(**values)

    @staticmethod
    def detail_url(card):
        """Return the canonical resource URL for a work order."""
        return f'/api/tasks/work-orders/{card.pk}/'

    @staticmethod
    def transition_url(card):
        """Return the canonical transition-command URL for a work order."""
        return f'/api/tasks/work-orders/{card.pk}/transition/'

    def test_list_is_paginated_and_excludes_other_customer(self):
        """Collection counts and rows must not leak another customer scope."""
        visible = self.make_card(title='Visible work order')
        self.make_card(
            title='Hidden work order',
            customer=self.other_customer,
        )

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(set(payload), {'count', 'next', 'previous', 'results'})
        self.assertEqual(payload['count'], 1)
        self.assertEqual([item['id'] for item in payload['results']], [visible.pk])

    def test_detail_returns_typed_work_order_fields(self):
        """The canonical detail exposes typed IDs, state, kind, and version."""
        technician = get_user_model().objects.create_user(
            username='typed-api-technician'
        )
        card = self.make_card(
            reference='WO-API-0001',
            lifecycle_status=WorkOrderLifecycle.PLANNED,
            work_order_type=WorkOrderType.PREVENTIVE,
            assigned_to=technician,
            requested_by=self.actor,
            estimated_minutes=90,
            lifecycle_version=4,
        )

        response = self.client.get(self.detail_url(card))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(payload['id'], card.pk)
        self.assertEqual(payload['reference'], 'WO-API-0001')
        self.assertEqual(payload['lifecycle_status'], WorkOrderLifecycle.PLANNED)
        self.assertEqual(payload['work_order_type'], WorkOrderType.PREVENTIVE)
        self.assertEqual(payload['customer'], self.customer.pk)
        self.assertEqual(payload['assigned_to'], technician.pk)
        self.assertEqual(payload['requested_by'], self.actor.pk)
        self.assertEqual(payload['estimated_minutes'], 90)
        self.assertEqual(payload['lifecycle_version'], 4)
        self.assertEqual(payload['status'], KanbanCard.STATUS_BACKLOG)

    def test_generic_patch_cannot_change_lifecycle_or_version(self):
        """Planning PATCH may update metadata but cannot act as a command."""
        card = self.make_card()

        response = self.client.patch(
            self.detail_url(card),
            {
                'title': 'Updated planning title',
                'lifecycle_status': WorkOrderLifecycle.COMPLETED,
                'lifecycle_version': 99,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        card.refresh_from_db()
        self.assertEqual(card.title, 'Updated planning title')
        self.assertEqual(card.lifecycle_status, WorkOrderLifecycle.DRAFT)
        self.assertEqual(card.lifecycle_version, 1)
        self.assertEqual(response.json()['lifecycle_status'], WorkOrderLifecycle.DRAFT)
        self.assertEqual(response.json()['lifecycle_version'], 1)
        self.assertFalse(card.events.exists())

    def test_transition_advances_once_and_exact_replay_is_idempotent(self):
        """An exact command replay returns the durable result without new effects."""
        card = self.make_card()
        command = {
            'to_status': WorkOrderLifecycle.PLANNED,
            'expected_version': 1,
            'idempotency_key': 'api-transition-replay',
            'reason': 'Planning is complete',
        }

        first = self.client.post(self.transition_url(card), command, format='json')
        replay = self.client.post(self.transition_url(card), command, format='json')

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(replay.json(), first.json())
        result = first.json()
        self.assertEqual(result['work_order_id'], card.pk)
        self.assertEqual(result['command'], 'transition')
        self.assertEqual(result['lifecycle_status'], WorkOrderLifecycle.PLANNED)
        self.assertEqual(result['lifecycle_version'], 2)
        self.assertEqual(result['idempotency_key'], 'api-transition-replay')
        card.refresh_from_db()
        self.assertEqual(card.lifecycle_status, WorkOrderLifecycle.PLANNED)
        self.assertEqual(card.lifecycle_version, 2)
        self.assertEqual(WorkOrderEvent.objects.filter(work_order=card).count(), 1)
        self.assertEqual(WorkOrderCommand.objects.filter(work_order=card).count(), 1)

    def test_readiness_returns_structured_blockers(self):
        """Readiness reports every discovered blocker in the stable flat shape."""
        card = self.make_card(lifecycle_status=WorkOrderLifecycle.READY)

        response = self.client.get(
            f'/api/tasks/work-orders/{card.pk}/readiness/', {'action': 'start'}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(
            set(payload),
            {
                'action',
                'ready',
                'evaluated_at',
                'lifecycle_version',
                'policy_version',
                'blockers',
                'warnings',
                'snapshot_hash',
            },
        )
        self.assertEqual(payload['action'], 'start')
        self.assertFalse(payload['ready'])
        self.assertEqual(payload['lifecycle_version'], 1)
        self.assertEqual(payload['warnings'], [])
        self.assertEqual(
            {blocker['code'] for blocker in payload['blockers']},
            {ASSET_REQUIRED, ASSIGNEE_REQUIRED},
        )
        self.assertTrue(
            all(
                set(blocker)
                == {
                    'code',
                    'message',
                    'source',
                    'object_type',
                    'object_id',
                    'blocking',
                    'remediation',
                    'metadata',
                }
                for blocker in payload['blockers']
            )
        )

    def test_blocked_transition_uses_command_error_envelope(self):
        """Readiness rejection returns blockers with concurrency context."""
        card = self.make_card(lifecycle_status=WorkOrderLifecycle.READY)

        response = self.client.post(
            self.transition_url(card),
            {
                'to_status': WorkOrderLifecycle.IN_PROGRESS,
                'expected_version': 1,
                'idempotency_key': 'api-start-blocked',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        payload = response.json()
        self.assertEqual(
            set(payload),
            {'code', 'detail', 'correlation_id', 'current_version', 'blockers'},
        )
        self.assertEqual(payload['code'], ASSET_REQUIRED)
        self.assertEqual(payload['current_version'], 1)
        self.assertEqual(
            {blocker['code'] for blocker in payload['blockers']},
            {ASSET_REQUIRED, ASSIGNEE_REQUIRED},
        )
        card.refresh_from_db()
        self.assertEqual(card.lifecycle_status, WorkOrderLifecycle.READY)
        self.assertEqual(card.lifecycle_version, 1)
        self.assertFalse(card.events.exists())

    def test_packet_owned_transition_returns_stable_conflict(self):
        """Direct lifecycle commands fail when a Repair Packet owns the state."""
        card = self.make_card()
        RepairPacket.objects.create(work_order=card, created_by=self.actor)

        response = self.client.post(
            self.transition_url(card),
            {
                'to_status': WorkOrderLifecycle.PLANNED,
                'expected_version': 1,
                'idempotency_key': 'api-packet-owned',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        payload = response.json()
        self.assertEqual(
            set(payload),
            {'code', 'detail', 'correlation_id', 'current_version', 'blockers'},
        )
        self.assertEqual(payload['code'], PACKET_OWNS_LIFECYCLE)
        self.assertEqual(payload['current_version'], 1)
        self.assertEqual(payload['blockers'], [])
        card.refresh_from_db()
        self.assertEqual(card.lifecycle_status, WorkOrderLifecycle.DRAFT)
        self.assertEqual(card.lifecycle_version, 1)
        self.assertFalse(card.events.exists())

    def test_kanban_list_exposes_the_unified_card_shape(self):
        """Kanban and the canonical API now share one card shape.

        This test previously asserted the inverse -- that typed work-order fields
        must *not* appear here -- to stop the flag-gated canonical API leaking
        through an unflagged surface. That boundary was retired deliberately once
        it was established there are no external clients of this API: the board,
        calendar and timeline all read this one endpoint, so maintaining a second,
        narrower shape bought nothing and cost a reconciliation.

        What survives from the old boundary is the read/write split, asserted in
        ``test_typed_fields_are_read_only_through_kanban`` below: exposing
        lifecycle state for reading is not the same as letting a board edit drive
        it.
        """
        card = self.make_card(
            title='Legacy shape card',
            assignee='Legacy Technician',
            tags=['legacy', 'contract'],
            company='Legacy Company Text',
        )

        response = self.client.get('/api/kanban/cards/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 1)
        self.assertEqual(
            set(payload[0]),
            {
                'id',
                'title',
                'description',
                'status',
                'priority',
                'due_date',
                'assignee',
                'tags',
                'company',
                'company_contact_name',
                'company_contact_phone',
                'job_number',
                'service_quote',
                'is_active',
                'created_at',
                'updated_at',
                'parts',
                # Planning metadata, added for the scheduling views. These are
                # inert scalars the board can place on a calendar; they carry no
                # lifecycle semantics and do not depend on the canonical API being
                # enabled.
                'machine',
                'machine_name',
                'machine_location',
                'assigned_to',
                'assigned_to_username',
                'assigned_to_name',
                'scheduled_start',
                'scheduled_end',
                'estimated_minutes',
                'work_order_type',
                'reference',
                'lifecycle_status',
                'lifecycle_version',
                'actual_started_at',
                'actual_completed_at',
                # Composition (§5.10): parent link and card kind.
                'parent',
                'card_kind',
            },
        )
        self.assertEqual(payload[0]['id'], card.pk)
        self.assertEqual(payload[0]['status'], KanbanCard.STATUS_BACKLOG)
        self.assertEqual(payload[0]['lifecycle_status'], WorkOrderLifecycle.DRAFT)
        # lifecycle_version is published because Phase 3 uses it as the
        # expected_version optimistic-concurrency token.
        self.assertEqual(payload[0]['lifecycle_version'], 1)
        # Still withheld: internal fields with no board meaning.
        for internal_field in ('requested_by', 'hold_reason'):
            self.assertNotIn(internal_field, payload[0])

    def test_typed_fields_are_read_only_through_kanban(self):
        """Reading lifecycle state is not the same as being able to drive it."""
        card = self.make_card(title='Read-only typed fields')

        response = self.client.patch(
            f'/api/kanban/cards/{card.pk}/',
            data={
                'lifecycle_status': WorkOrderLifecycle.COMPLETED,
                'lifecycle_version': 99,
                'reference': 'WO-FORGED',
                'actual_completed_at': '2026-08-01T00:00:00Z',
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        card.refresh_from_db()
        self.assertEqual(card.lifecycle_status, WorkOrderLifecycle.DRAFT)
        self.assertEqual(card.lifecycle_version, 1)
        self.assertIsNone(card.actual_completed_at)
