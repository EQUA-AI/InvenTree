"""S26: the deterministic similar-past-repairs diagnostic reader.

Candidates are packets whose work order carries a VERIFIED closeout, on the
authorized machine or same-model machines in the same client. Scoring is
2 x shared failure codes + 1 x shared finding categories against the current
packet; no embeddings, no free-text matching.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from tasks.models import WorkOrder, WorkOrderCloseout, WorkOrderLifecycle, WorkOrderType
from tasks.scope import MaintenanceScope

from assets.models import AssetMachine, Client
from repair import services

from .models import (
    ApprovedRepairScope,
    PacketStatus,
    RepairInvestigationFinding,
    RepairPacket,
)

MAINTENANCE_CAPABILITY = 'diagnostics.maintenance.read'

_GRANTED_CLIENT_IDS: list[int] = []


def _capabilities(actor):
    """Grant the full diagnostic capability set to the test actor."""
    del actor
    return services._DIAGNOSTIC_CAPABILITIES


def _scopes(actor):
    """Resolve the per-test client scopes."""
    del actor
    return {
        MaintenanceScope(customer_id=None, site_key=None, client_id=client_id)
        for client_id in _GRANTED_CLIENT_IDS
    }


class _Authorization:
    """The decision the registry passes to a reader, as an attribute object."""

    def __init__(self, decision):
        for key, value in decision.items():
            setattr(self, key, value)
        self.linked_machine_id = decision.get('linked_machine_id')


@override_settings(
    AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER='repair.test_similar_past_repairs._capabilities',
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='repair.test_similar_past_repairs._scopes',
)
class SimilarPastRepairsReadTest(TestCase):
    """Verified-only, client-scoped, deterministically ranked."""

    def setUp(self):
        """One machine with a current packet and a spread of past repairs."""
        suffix = uuid.uuid4().hex[:8]
        self.actor = get_user_model().objects.create_superuser(
            username=f'similar-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.client_tenant = Client.objects.create(
            name=f'Tenant {suffix}', code=f'tenant-{suffix}'
        )
        self.other_tenant = Client.objects.create(
            name=f'Other {suffix}', code=f'other-{suffix}'
        )
        _GRANTED_CLIENT_IDS[:] = [self.client_tenant.pk]
        self.addCleanup(_GRANTED_CLIENT_IDS.clear)

        self.machine = AssetMachine.objects.create(
            name=f'Pump A {suffix}', model='NP 3301 MT', client=self.client_tenant
        )
        self.sibling = AssetMachine.objects.create(
            name=f'Pump B {suffix}', model='NP 3301 MT', client=self.client_tenant
        )
        self.foreign = AssetMachine.objects.create(
            name=f'Pump C {suffix}', model='NP 3301 MT', client=self.other_tenant
        )

        self.current = RepairPacket.objects.create(
            machine=self.machine,
            fault_summary='Seal leaking at drive end',
            status=PacketStatus.DIAGNOSED,
        )
        ApprovedRepairScope.objects.create(
            packet=self.current, version=1, failure_codes=['AL-SEAL-LEAK']
        )
        RepairInvestigationFinding.objects.create(
            packet=self.current,
            finding_key='vibration',
            category=RepairInvestigationFinding.Category.MEASUREMENT,
            observation='Vibration 7.4 mm/s at the drive end',
        )

    def _closed_packet(
        self, *, machine, codes=(), category=None, verified=True, summary='Past repair'
    ):
        """One past packet with a closeout, optionally verified."""
        suffix = uuid.uuid4().hex[:8]
        work_order = WorkOrder.objects.create(
            title=f'{summary} {suffix}',
            machine=machine,
            work_order_type=WorkOrderType.CORRECTIVE,
            lifecycle_status=WorkOrderLifecycle.COMPLETED,
        )
        packet = RepairPacket.objects.create(
            machine=machine,
            work_order=work_order,
            fault_summary=summary,
            status=PacketStatus.CLOSED,
        )
        if codes:
            ApprovedRepairScope.objects.create(
                packet=packet, version=1, failure_codes=list(codes)
            )
        if category is not None:
            RepairInvestigationFinding.objects.create(
                packet=packet,
                finding_key='observation',
                category=category,
                observation='Recorded during the past repair',
            )
        now = timezone.now()
        WorkOrderCloseout.objects.create(
            work_order=work_order,
            cause='Worn part',
            action='Replaced the worn part',
            result='Back to baseline',
            verification_summary='Stable run verified',
            completed_by=self.actor,
            completed_at=now,
            verified_by=self.actor if verified else None,
            verified_at=now if verified else None,
            content_hash=f'hash-{suffix}',
        )
        return packet

    def _read(self, *, repair_packet_id=None, machine=None):
        """Authorize the machine root and run the reader."""
        machine = machine or self.machine
        revision = services._diagnostic_revision(machine)
        decision = services.authorize_diagnostic_read(
            actor=self.actor,
            capability=MAINTENANCE_CAPABILITY,
            entity_type='machine',
            entity_id=machine.pk,
            expected_revision=revision,
            linked_machine_id=None,
            check_id=uuid.uuid4().hex,
        )
        self.assertIsNotNone(decision, 'test actor should be authorized')
        return services.read_diagnostic_similar_past_repairs(
            actor=self.actor,
            authorization=_Authorization(decision),
            machine_id=machine.pk,
            expected_revision=revision,
            repair_packet_id=repair_packet_id,
        )

    def test_scoring_ranks_shared_codes_above_shared_categories(self):
        """2 x failure-code overlap beats 1 x category overlap beats nothing."""
        code_sharer = self._closed_packet(
            machine=self.machine, codes=['AL-SEAL-LEAK', 'AL-OVERTEMP']
        )
        category_sharer = self._closed_packet(
            machine=self.machine,
            codes=['AL-CLOG'],
            category=RepairInvestigationFinding.Category.MEASUREMENT,
        )
        unrelated = self._closed_packet(machine=self.sibling)

        result = self._read(repair_packet_id=self.current.pk)
        claims = [item['claim'] for item in result['evidence']]

        self.assertEqual(len(claims), 3)
        self.assertIn(code_sharer.fault_summary, claims[0])
        self.assertIn('"shared_failure_codes": ["AL-SEAL-LEAK"]', claims[0])
        self.assertIn('"similarity_score": 2', claims[0])
        self.assertIn('"shared_finding_categories": ["measurement"]', claims[1])
        self.assertIn('"similarity_score": 1', claims[1])
        self.assertIn(f'"machine_id": {self.sibling.pk}', claims[2])
        del category_sharer, unrelated
        for item in result['evidence']:
            self.assertEqual(item['source_type'], 'work_order_closeout')
            self.assertTrue(item['untrusted'])

    def test_unverified_closeouts_never_qualify(self):
        """An unverified closeout is a claim, not history."""
        self._closed_packet(machine=self.machine, verified=False)
        result = self._read(repair_packet_id=self.current.pk)
        self.assertEqual(result['evidence'], ())
        self.assertTrue(result['abstention_reason'])

    def test_other_tenants_same_model_machines_are_invisible(self):
        """Model similarity never crosses the client boundary."""
        self._closed_packet(machine=self.foreign)
        result = self._read(repair_packet_id=self.current.pk)
        self.assertEqual(result['evidence'], ())

    def test_foreign_packet_candidate_abstains(self):
        """A packet id outside the authorized machine is refused."""
        foreign_packet = RepairPacket.objects.create(
            machine=self.sibling, fault_summary='Different machine'
        )
        self._closed_packet(machine=self.machine, codes=['AL-SEAL-LEAK'])
        result = self._read(repair_packet_id=foreign_packet.pk)
        self.assertEqual(result['evidence'], ())
        self.assertTrue(result['abstention_reason'])

    def test_without_a_current_packet_ranking_is_recency(self):
        """No fault signature degrades to newest-verified-first, verified-only."""
        older = self._closed_packet(machine=self.machine, summary='Older repair')
        newer = self._closed_packet(machine=self.machine, summary='Newer repair')
        self._closed_packet(machine=self.machine, verified=False, summary='Unverified')

        result = self._read()
        claims = [item['claim'] for item in result['evidence']]
        self.assertEqual(len(claims), 2)
        self.assertIn(newer.fault_summary, claims[0])
        self.assertIn(older.fault_summary, claims[1])
