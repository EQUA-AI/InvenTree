"""The compound repair work-package proposal action.

Chat can investigate and propose; it cannot create. The only thing that turns a
proposal into a repair is a human confirming it, and confirmation runs the same
audited command the Maintenance button and the Health blade run - so the AI path
has no privileges the UI path lacks, and no way to skip a check the UI cannot
skip either.
"""

from __future__ import annotations

import unittest
import uuid

from django.apps import apps

if not apps.is_installed('tasks'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.contrib.auth import get_user_model
from django.test import TestCase

from tasks.models import WorkOrder
from tasks.scope import MaintenanceScope

from aichat.models import ProposalState
from aichat.services import proposals as svc
from assets.models import AssetMachine, Client
from part.models import Part
from repair.models import RepairPacket

ACTION = 'repair_work_package.create'


class RepairWorkPackageProposalTest(TestCase):
    """Propose, preview and confirm one repair work package."""

    def setUp(self):
        """Create a scoped actor, a machine and a stockable part."""
        suffix = uuid.uuid4().hex[:8]
        self.client_tenant = Client.objects.create(
            name=f'Tenant {suffix}', code=f'tenant-{suffix}'
        )
        self.actor = get_user_model().objects.create_superuser(
            username=f'chat-planner-{suffix}',
            email=f'{suffix}@example.com',
            password='pw',
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_tenant.pk
            )
        }
        self.machine = AssetMachine.objects.create(
            name=f'Centrifuge {suffix}', client=self.client_tenant
        )
        self.part = Part.objects.create(
            name=f'Bearing {suffix}', description='spare', component=True
        )

    def _intent(self, **overrides):
        intent = {
            'machine_id': self.machine.pk,
            'title': 'Investigate rising bowl vibration',
            'origin': 'chat',
            'fault': {
                'summary': 'Vibration climbing across the last three runs',
                'criticality': 'high',
            },
            'parts': [{'part_id': self.part.pk, 'quantity': 2}],
        }
        intent.update(overrides)
        return intent

    def _propose(self, key=None, **overrides):
        return svc.create_proposal(
            owner=self.actor,
            scope_key=f'client:{self.client_tenant.pk}',
            scope_hash='b' * 64,
            action_type=ACTION,
            work_order_id=None,
            reason='Discussed in chat: raise a repair for the vibration trend.',
            idempotency_key=key or uuid.uuid4().hex,
            policy_version='test-v1',
            intent=self._intent(**overrides),
            thread_id='thread-1',
            source_turn_id='turn-7',
        )

    def test_proposing_creates_nothing(self):
        """A proposal is a request for approval, not an effect."""
        before_cards = WorkOrder.objects.count()
        before_packets = RepairPacket.objects.count()

        proposal = self._propose()

        self.assertEqual(proposal.state, ProposalState.PROPOSED)
        self.assertEqual(WorkOrder.objects.count(), before_cards)
        self.assertEqual(RepairPacket.objects.count(), before_packets)

    def test_preview_is_derived_from_server_state(self):
        """The approver reads database facts, not the model's description."""
        proposal = self._propose()
        preview = proposal.preview

        self.assertEqual(preview['machine_name'], self.machine.name)
        self.assertEqual(preview['parts'][0]['name'], self.part.name)
        self.assertTrue(preview['creates_repair_packet'])
        self.assertTrue(preview['creates_planned_work_only'])
        self.assertIn('does not start the repair', preview['note'])

    def test_preview_warns_about_an_existing_open_repair(self):
        """Duplicate work is surfaced before approval, not after."""
        from repair.work_packages import create_repair_work_package

        existing = create_repair_work_package(
            actor=self.actor,
            draft={'machine_id': self.machine.pk, 'title': 'Already open'},
            idempotency_key=uuid.uuid4().hex,
        )

        proposal = self._propose()

        duplicates = proposal.preview['duplicate_open_repairs']
        self.assertTrue(duplicates)
        self.assertEqual(
            duplicates[0]['work_order_id'], existing.work_order_id
        )

    def test_confirmation_creates_one_linked_aggregate(self):
        """Approval runs the same audited command the UI runs."""
        proposal = self._propose()

        confirmed = svc.confirm_proposal(
            owner=self.actor, scope_hash='b' * 64, proposal_id=proposal.id
        )

        self.assertEqual(confirmed.state, ProposalState.EXECUTED)
        receipt = confirmed.receipt
        self.assertEqual(receipt['command'], 'create_repair_work_package')

        work_order = WorkOrder.objects.get(pk=receipt['work_order_id'])
        packet = RepairPacket.objects.get(pk=receipt['repair_packet_id'])
        self.assertEqual(work_order.machine_id, self.machine.pk)
        self.assertEqual(packet.machine_id, self.machine.pk)
        self.assertEqual(packet.work_order_id, work_order.pk)

    def test_confirming_twice_produces_one_effect(self):
        """A replayed confirmation returns the stored receipt, not a second repair."""
        proposal = self._propose()

        first = svc.confirm_proposal(
            owner=self.actor, scope_hash='b' * 64, proposal_id=proposal.id
        )
        second = svc.confirm_proposal(
            owner=self.actor, scope_hash='b' * 64, proposal_id=proposal.id
        )

        self.assertEqual(first.receipt, second.receipt)
        self.assertEqual(
            WorkOrder.objects.filter(machine=self.machine).count(), 1
        )
        self.assertEqual(
            RepairPacket.objects.filter(machine=self.machine).count(), 1
        )

    def test_rejected_proposal_creates_no_work(self):
        """Denial leaves the machine untouched."""
        proposal = self._propose()

        svc.reject_proposal(
            owner=self.actor, scope_hash='b' * 64, proposal_id=proposal.id
        )

        self.assertFalse(WorkOrder.objects.filter(machine=self.machine).exists())
        self.assertFalse(RepairPacket.objects.filter(machine=self.machine).exists())

    def test_a_machine_outside_scope_is_not_proposable(self):
        """Scope is applied when the proposal is raised, not at execution."""
        other_suffix = uuid.uuid4().hex[:6]
        other_client = Client.objects.create(
            name=f'Tenant {other_suffix}', code=f'tenant-{other_suffix}'
        )
        other_machine = AssetMachine.objects.create(
            name=f'Foreign {other_suffix}', client=other_client
        )

        with self.assertRaises(svc.ProposalNotFound):
            self._propose(machine_id=other_machine.pk)

    def test_internal_client_asset_is_proposable(self):
        """An asset nobody bought is reachable through its client.

        This is the gap the client identity closes: before it, an internal plant
        machine had no scope at all and chat refused to touch it.
        """
        suffix = uuid.uuid4().hex[:6]
        client = Client.objects.create(name=f'Plant {suffix}', code=f'plant-{suffix}')
        internal = AssetMachine.objects.create(
            name=f'Internal {suffix}', client=client
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=client.pk)
        }

        proposal = self._propose(machine_id=internal.pk)

        self.assertEqual(proposal.preview['machine_name'], internal.name)

    def test_machine_with_no_scope_identity_still_fails_closed(self):
        """A boundary is never guessed for an unscoped record."""
        orphan = AssetMachine.objects.create(name=f'Orphan {uuid.uuid4().hex[:6]}')

        with self.assertRaisesMessage(
            svc.ProposalError, 'Machine scope is unresolved: it has no client.'
        ):
            self._propose(machine_id=orphan.pk)

    def test_invalid_draft_is_refused_before_any_row_is_written(self):
        """A malformed draft never becomes a pending proposal."""
        with self.assertRaises(Exception):
            self._propose(title='   ')

        self.assertFalse(WorkOrder.objects.filter(machine=self.machine).exists())

    def test_confirming_into_an_open_repair_fails_closed(self):
        """A duplicate confirm surfaces the open repair and writes nothing new.

        The preview already warned; if the approver confirms anyway without an
        explicit override, dispatch refuses with the same links the preview
        showed and the proposal lands in a terminal FAILED state.
        """
        from repair.work_packages import create_repair_work_package

        existing = create_repair_work_package(
            actor=self.actor,
            draft={'machine_id': self.machine.pk, 'title': 'Already open'},
            idempotency_key=uuid.uuid4().hex,
        )
        proposal = self._propose()
        before_orders = WorkOrder.objects.count()
        before_packets = RepairPacket.objects.count()

        with self.assertRaises(svc.ProposalDuplicateConflict) as caught:
            svc.confirm_proposal(
                owner=self.actor, scope_hash='b' * 64, proposal_id=proposal.id
            )

        self.assertTrue(caught.exception.duplicates)
        self.assertEqual(
            caught.exception.duplicates[0]['work_order_id'],
            existing.work_order_id,
        )
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, ProposalState.FAILED)
        self.assertEqual(proposal.failure_code, 'DUPLICATE_OPEN_REPAIR')
        self.assertEqual(WorkOrder.objects.count(), before_orders)
        self.assertEqual(RepairPacket.objects.count(), before_packets)

    def test_duplicate_override_confirms_alongside_the_open_repair(self):
        """An explicit override proceeds past the open repair, attributed.

        Proceeding is a deliberate act carried in the intent, so the second
        aggregate is created and the receipt records that it was created
        alongside existing open work.
        """
        from repair.work_packages import create_repair_work_package

        create_repair_work_package(
            actor=self.actor,
            draft={'machine_id': self.machine.pk, 'title': 'Already open'},
            idempotency_key=uuid.uuid4().hex,
        )
        proposal = self._propose(
            duplicate_override=True,
            duplicate_override_reason='Second independent fault, crew agreed.',
        )

        confirmed = svc.confirm_proposal(
            owner=self.actor, scope_hash='b' * 64, proposal_id=proposal.id
        )

        self.assertEqual(confirmed.state, ProposalState.EXECUTED)
        receipt = confirmed.receipt
        self.assertEqual(receipt['command'], 'create_repair_work_package')
        self.assertTrue(
            any('existing open repair' in warning for warning in receipt['warnings'])
        )
        self.assertEqual(
            RepairPacket.objects.filter(machine=self.machine).count(), 2
        )
        self.assertEqual(
            WorkOrder.objects.filter(machine=self.machine).count(), 2
        )

    def test_permission_parity_with_the_ui_path(self):
        """Without work_order.add the proposal cannot even be raised."""
        weak = get_user_model().objects.create_user(
            username=f'weak-{uuid.uuid4().hex[:8]}',
            email='weak@example.com',
            password='pw',
        )
        weak.maintenance_scopes = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_tenant.pk
            )
        }

        with self.assertRaises(svc.CapabilityDenied):
            svc.create_proposal(
                owner=weak,
                scope_key=f'client:{self.client_tenant.pk}',
                scope_hash='b' * 64,
                action_type=ACTION,
                work_order_id=None,
                reason='no authority',
                idempotency_key=uuid.uuid4().hex,
                policy_version='test-v1',
                intent=self._intent(),
            )
