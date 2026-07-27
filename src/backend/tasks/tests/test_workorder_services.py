"""Unit tests for maintenance work-order command services."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from company.models import Company
from repair.models import RepairPacket
from tasks.models import WorkOrder, WorkOrderEvent, WorkOrderLifecycle
from tasks.scope import MaintenanceScope
from tasks.services.readiness import PACKET_OWNS_LIFECYCLE
from tasks.services.work_orders import (
    CommandConflict,
    IllegalTransition,
    StaleVersion,
    assign_work_order,
    transition_work_order,
)


class WorkOrderServiceTest(TestCase):
    """Exercise the additive ME1 command boundary directly through the ORM."""

    def setUp(self):
        """Create a privileged, explicitly scoped command actor."""
        self.customer = Company.objects.create(
            name='ME1 Customer', is_customer=True
        )
        self.actor = get_user_model().objects.create_superuser(
            username='me1-supervisor', email='me1@example.com', password='test-password'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }

    def make_card(self, **overrides):
        """Create a minimally valid, customer-scoped work order."""
        values = {
            'title': 'ME1 work order',
            'status': WorkOrder.STATUS_BACKLOG,
            'priority': WorkOrder.PRIORITY_MEDIUM,
            'customer': self.customer,
        }
        values.update(overrides)
        return WorkOrder.objects.create(**values)

    def test_legal_transition_advances_version_and_event_once(self):
        work_order = self.make_card()

        result = transition_work_order(
            work_order_id=work_order.pk,
            to_status=WorkOrderLifecycle.PLANNED,
            actor=self.actor,
            expected_version=1,
            idempotency_key='legal-transition',
        )

        work_order.refresh_from_db()
        self.assertEqual(work_order.lifecycle_status, WorkOrderLifecycle.PLANNED)
        self.assertEqual(work_order.lifecycle_version, 2)
        self.assertEqual(work_order.events.count(), 1)
        self.assertEqual(result.lifecycle_version, 2)

    def test_illegal_transition_is_rejected_without_event(self):
        work_order = self.make_card()

        with self.assertRaises(IllegalTransition):
            transition_work_order(
                work_order_id=work_order.pk,
                to_status=WorkOrderLifecycle.COMPLETED,
                actor=self.actor,
                expected_version=1,
                idempotency_key='illegal-transition',
            )

        work_order.refresh_from_db()
        self.assertEqual(work_order.lifecycle_status, WorkOrderLifecycle.DRAFT)
        self.assertEqual(work_order.lifecycle_version, 1)
        self.assertFalse(work_order.events.exists())

    def test_stale_expected_version_is_rejected(self):
        work_order = self.make_card(lifecycle_version=3)

        with self.assertRaises(StaleVersion):
            transition_work_order(
                work_order_id=work_order.pk,
                to_status=WorkOrderLifecycle.PLANNED,
                actor=self.actor,
                expected_version=2,
                idempotency_key='stale-transition',
            )

        self.assertFalse(work_order.events.exists())

    def test_idempotent_replay_returns_prior_result_without_duplicate_event(self):
        work_order = self.make_card()
        arguments = {
            'work_order_id': work_order.pk,
            'to_status': WorkOrderLifecycle.PLANNED,
            'actor': self.actor,
            'expected_version': 1,
            'idempotency_key': 'replayed-transition',
        }

        first = transition_work_order(**arguments)
        replay = transition_work_order(**arguments)

        work_order.refresh_from_db()
        self.assertEqual(replay, first)
        self.assertEqual(work_order.lifecycle_version, 2)
        self.assertEqual(work_order.events.count(), 1)
        self.assertEqual(work_order.commands.count(), 1)

    def test_packet_link_rejects_direct_lifecycle_transition(self):
        work_order = self.make_card()
        RepairPacket.objects.create(work_order=work_order, created_by=self.actor)

        with self.assertRaises(CommandConflict) as caught:
            transition_work_order(
                work_order_id=work_order.pk,
                to_status=WorkOrderLifecycle.PLANNED,
                actor=self.actor,
                expected_version=1,
                idempotency_key='packet-owned',
            )

        self.assertEqual(caught.exception.code, PACKET_OWNS_LIFECYCLE)
        work_order.refresh_from_db()
        self.assertEqual(work_order.lifecycle_status, WorkOrderLifecycle.DRAFT)
        self.assertFalse(work_order.events.exists())

    def test_assign_preserves_legacy_assignee_text(self):
        work_order = self.make_card(assignee='Legacy Technician')
        technician = get_user_model().objects.create_user(username='typed-technician')

        assign_work_order(
            work_order_id=work_order.pk,
            assigned_to=technician,
            actor=self.actor,
            expected_version=1,
            idempotency_key='assign-technician',
        )

        work_order.refresh_from_db()
        self.assertEqual(work_order.assigned_to, technician)
        self.assertEqual(work_order.assignee, 'Legacy Technician')
        self.assertEqual(work_order.lifecycle_version, 2)
        self.assertEqual(work_order.events.get().event_type, 'ASSIGNED')

    def test_board_status_change_does_not_change_lifecycle(self):
        work_order = self.make_card()

        work_order.status = WorkOrder.STATUS_DONE
        work_order.save(update_fields=['status'])

        work_order.refresh_from_db()
        self.assertEqual(work_order.status, WorkOrder.STATUS_DONE)
        self.assertEqual(work_order.lifecycle_status, WorkOrderLifecycle.DRAFT)
        self.assertEqual(work_order.lifecycle_version, 1)
        self.assertEqual(WorkOrderEvent.objects.filter(work_order=work_order).count(), 0)
