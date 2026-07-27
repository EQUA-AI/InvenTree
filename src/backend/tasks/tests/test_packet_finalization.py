"""The packet-finalization capability cannot be claimed from outside.

Suppressing the packet-ownership check is the one privilege that lets a work
order be finalized outside the standalone commands. Only ``repair.services`` may
exercise it, and only for the packet it holds locked - everything below pins
that the privilege is not something a caller can simply assert.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from rest_framework import status
from rest_framework.test import APIClient

from company.models import Company
from repair.models import RepairPacket
from tasks.models import KanbanCard, WorkOrderLifecycle
from tasks.scope import MaintenanceScope
from tasks.services.finalization import PacketFinalization, is_packet_finalization
from tasks.services.readiness import PACKET_OWNS_LIFECYCLE
from tasks.services.work_orders import CommandConflict, transition_work_order
from tasks.workorder_api import RESERVED_SERVICE_ARGUMENTS, WorkOrderCommandView


class PacketFinalizationTokenTest(TestCase):
    """Only a token minted for the owning packet authorizes anything."""

    def setUp(self):
        """Create an actor, a work order and the packet that owns it."""
        suffix = uuid.uuid4().hex[:6]
        self.customer = Company.objects.create(
            name=f'Finalization {suffix}', is_customer=True
        )
        self.actor = get_user_model().objects.create_superuser(
            username=f'fin-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.card = KanbanCard.objects.create(
            title='Packet-owned work',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_HIGH,
            customer=self.customer,
        )
        self.packet = RepairPacket.objects.create(
            work_order=self.card, created_by=self.actor
        )

    def test_a_matching_token_authorizes(self):
        """The packet that owns the work order may finalize it."""
        self.assertTrue(
            is_packet_finalization(
                PacketFinalization(packet_id=self.packet.pk), self.card
            )
        )

    def test_true_is_not_a_token(self):
        """This is the shape a request-borne value would arrive in."""
        for value in (True, 1, 'true', {'packet_id': self.packet.pk}):
            with self.subTest(value=value):
                self.assertFalse(is_packet_finalization(value, self.card))

    def test_a_token_for_another_packet_does_not_authorize(self):
        """A capability is scoped to the packet it was minted for."""
        other_card = KanbanCard.objects.create(
            title='Someone else',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_LOW,
            customer=self.customer,
        )
        other = RepairPacket.objects.create(
            work_order=other_card, created_by=self.actor
        )

        self.assertFalse(
            is_packet_finalization(PacketFinalization(packet_id=other.pk), self.card)
        )

    def test_a_token_does_not_authorize_unowned_work(self):
        """A standalone work order has no packet to finalize on its behalf."""
        standalone = KanbanCard.objects.create(
            title='Standalone',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_LOW,
            customer=self.customer,
        )

        self.assertFalse(
            is_packet_finalization(
                PacketFinalization(packet_id=self.packet.pk), standalone
            )
        )

    def test_a_forged_token_still_fails_at_the_command(self):
        """The check is enforced where it matters, not only in the helper."""
        with self.assertRaises(CommandConflict) as caught:
            transition_work_order(
                work_order_id=self.card.pk,
                to_status=WorkOrderLifecycle.PLANNED,
                actor=self.actor,
                expected_version=1,
                idempotency_key='forged',
                packet_finalization=True,
            )

        self.assertEqual(caught.exception.code, PACKET_OWNS_LIFECYCLE)
        self.card.refresh_from_db()
        self.assertEqual(self.card.lifecycle_status, WorkOrderLifecycle.DRAFT)


@override_settings(AIMMS_WORK_ORDERS_ENABLED=True)
class ReservedArgumentTest(TestCase):
    """The HTTP adapter cannot forward authority-bearing arguments."""

    def setUp(self):
        """Authenticate a superuser against a packet-owned work order."""
        suffix = uuid.uuid4().hex[:6]
        self.customer = Company.objects.create(
            name=f'Reserved {suffix}', is_customer=True
        )
        self.actor = get_user_model().objects.create_superuser(
            username=f'res-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.card = KanbanCard.objects.create(
            title='Packet-owned work',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_HIGH,
            customer=self.customer,
        )
        RepairPacket.objects.create(work_order=self.card, created_by=self.actor)

    def test_a_request_cannot_claim_packet_finalization(self):
        """Sending the flag in the body changes nothing about the answer."""
        response = self.client.post(
            f'/api/tasks/work-orders/{self.card.pk}/transition/',
            {
                'to_status': WorkOrderLifecycle.PLANNED,
                'expected_version': 1,
                'idempotency_key': 'http-forged',
                'packet_finalization': True,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.json()['code'], PACKET_OWNS_LIFECYCLE)

    def test_reserved_arguments_are_stripped_before_the_service(self):
        """A serializer that declared one could not smuggle it through.

        The current serializers declare none of these, so the API test above
        passes either way. This pins the adapter itself, which is what keeps
        that true after the next serializer is written.
        """
        view = WorkOrderCommandView()
        supplied = dict.fromkeys(RESERVED_SERVICE_ARGUMENTS, 'forged')
        supplied['reason'] = 'kept'

        forwarded = {
            name: value
            for name, value in view.service_arguments(supplied).items()
            if name not in RESERVED_SERVICE_ARGUMENTS
        }

        self.assertEqual(forwarded, {'reason': 'kept'})

    def test_no_command_serializer_declares_a_reserved_argument(self):
        """Catch the mistake at its source, not only at the boundary."""
        from tasks import workorder_api

        views = [
            attribute
            for attribute in vars(workorder_api).values()
            if isinstance(attribute, type)
            and issubclass(attribute, WorkOrderCommandView)
            and attribute.serializer_class is not None
        ]
        self.assertTrue(views)

        for view in views:
            declared = set(view.serializer_class().get_fields())
            with self.subTest(view=view.__name__):
                self.assertEqual(declared & RESERVED_SERVICE_ARGUMENTS, set())
