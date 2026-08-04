"""Tests for Safety Gates & LOTO S2 backend slice."""

from django.test import TestCase
from django.urls import reverse

from approvals.executors import SafetyGateExecutor
from approvals.models import ActionType, Approval
from InvenTree.unit_test import InvenTreeAPITestCase

from . import services
from .models import (
    GateStatus,
    LockoutPoint,
    PacketStatus,
    RepairPacket,
    RepairPacketApprovalLink,
    RepairPacketEvent,
    RepairPacketGate,
    SafetyEvidenceProof,
    SafetyGateTemplate,
)


def _create_electrical_template():
    return SafetyGateTemplate.objects.create(
        name='Test electrical lockout',
        gate_type='loto',
        applies_to={'fault_keywords': ['motor']},
    )


class SafetyTemplateResolutionTest(TestCase):
    """Template applicability and gate materialisation."""

    @classmethod
    def setUpTestData(cls):
        """Create shared test fixtures."""
        _create_electrical_template()

    def test_resolve_templates_creates_matching_gate_once(self):
        """Test resolve templates creates matching gate once."""
        packet = RepairPacket.objects.create(
            fault_summary='Motor contactor has no voltage',
            status=PacketStatus.DIAGNOSED,
        )
        created = services.resolve_safety_gates(packet)
        created_again = services.resolve_safety_gates(packet)

        self.assertGreaterEqual(created, 1)
        self.assertEqual(created_again, 0)
        self.assertTrue(packet.gates.filter(gate_type='loto').exists())
        self.assertTrue(
            packet.events.filter(
                event_type=RepairPacketEvent.EventType.GATES_RESOLVED
            ).exists()
        )

    def test_pending_blocking_template_gate_blocks_approval(self):
        """Test pending blocking template gate blocks approval."""
        packet = RepairPacket.objects.create(
            fault_summary='Motor breaker fault', status=PacketStatus.DIAGNOSED
        )
        services.resolve_safety_gates(packet)
        ok, detail = services.advance_packet(packet, PacketStatus.APPROVED)

        self.assertFalse(ok)
        self.assertIn('Safety gate', detail)


class SafetyGateActionTest(TestCase):
    """Gate confirm / verify / waive behaviour."""

    def test_required_photo_blocks_confirm_until_proof_exists(self):
        """Test required photo blocks confirm until proof exists."""
        packet = RepairPacket.objects.create(
            fault_summary='x', status=PacketStatus.DIAGNOSED
        )
        gate = RepairPacketGate.objects.create(
            packet=packet, name='Photo gate', requires_photo=True
        )

        ok, detail = services.confirm_gate(gate)
        self.assertFalse(ok)
        self.assertIn('photo', detail.lower())

        services.add_gate_proof(gate, SafetyEvidenceProof.ProofType.PHOTO)
        ok, detail = services.confirm_gate(gate)
        gate.refresh_from_db()
        self.assertTrue(ok, detail)
        self.assertEqual(gate.status, GateStatus.CONFIRMED)

    def test_loto_gate_blocks_confirm_until_points_verified(self):
        """Test loto gate blocks confirm until points verified."""
        packet = RepairPacket.objects.create(
            fault_summary='x', status=PacketStatus.DIAGNOSED
        )
        gate = RepairPacketGate.objects.create(
            packet=packet, name='LOTO', gate_type='loto'
        )
        point = LockoutPoint.objects.create(
            gate=gate,
            energy_source=LockoutPoint.EnergySource.ELECTRICAL,
            isolation_device='MCC-1 bucket',
            status=LockoutPoint.PointStatus.LOCKED,
        )

        ok, detail = services.confirm_gate(gate)
        self.assertFalse(ok)
        self.assertIn('lockout', detail.lower())

        point.status = LockoutPoint.PointStatus.VERIFIED
        point.save(update_fields=['status'])
        ok, detail = services.confirm_gate(gate)
        self.assertTrue(ok, detail)

    def test_second_person_verifier_must_differ(self):
        """Test second person verifier must differ."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username='loto-user')
        packet = RepairPacket.objects.create(fault_summary='x')
        gate = RepairPacketGate.objects.create(
            packet=packet, name='Two person gate', requires_second_person=True
        )
        gate.confirm(user=user)

        ok, detail = services.verify_gate(gate, user=user)
        self.assertFalse(ok)
        self.assertIn('different', detail.lower())

    def test_high_risk_waiver_creates_safety_approval(self):
        """Test high risk waiver creates safety approval."""
        packet = RepairPacket.objects.create(fault_summary='x')
        template = SafetyGateTemplate.objects.create(
            name='High risk safety gate', gate_type='loto', risk_tier=3
        )
        gate = RepairPacketGate.objects.create(
            packet=packet,
            template=template,
            name=template.name,
            gate_type=template.gate_type,
        )

        ok, detail = services.waive_gate(
            gate, reason='controlled exception', authority='EHS supervisor'
        )
        self.assertFalse(ok)
        self.assertIn('Safety approval required', detail)
        approval = Approval.objects.get(action_type=ActionType.SAFETY_GATE)
        self.assertTrue(
            RepairPacketApprovalLink.objects.filter(
                packet=packet, approval=approval, purpose='safety'
            ).exists()
        )

    def test_safety_gate_executor_waives_gate(self):
        """Test safety gate executor waives gate."""
        packet = RepairPacket.objects.create(fault_summary='x')
        gate = RepairPacketGate.objects.create(packet=packet, name='Executor gate')
        result = SafetyGateExecutor().execute(
            {
                'gate_id': gate.pk,
                'action': 'waive',
                'reason': 'approved waiver',
                'authority': 'EHS',
            },
            idempotency_key='safety-test-key',
        )
        gate.refresh_from_db()
        self.assertTrue(result.success, result.error_message)
        self.assertEqual(gate.status, GateStatus.WAIVED)
        self.assertEqual(gate.waiver_authority, 'EHS')


class SafetyGateAPITest(InvenTreeAPITestCase):
    """API behaviour for safety gates."""

    roles = 'all'

    def setUp(self):
        """Create shared test fixtures."""
        super().setUp()
        RepairPacket.objects.all().delete()

    def test_resolve_gates_endpoint(self):
        """Test resolve gates endpoint."""
        _create_electrical_template()
        packet = RepairPacket.objects.create(
            fault_summary='Motor coil voltage fault', status=PacketStatus.DIAGNOSED
        )
        url = reverse('repair-packet-resolve-gates', kwargs={'pk': packet.pk})
        response = self.post(url, {}, expected_code=200)

        self.assertGreaterEqual(response.data['created'], 1)
        self.assertGreaterEqual(len(response.data['gates']), 1)

    def test_proof_then_confirm_endpoint(self):
        """Test proof then confirm endpoint."""
        packet = RepairPacket.objects.create(fault_summary='x')
        gate = RepairPacketGate.objects.create(
            packet=packet, name='Photo gate', requires_photo=True
        )
        proof_url = reverse(
            'repair-packet-gate-proof', kwargs={'pk': packet.pk, 'gate_pk': gate.pk}
        )
        self.post(
            proof_url,
            {'proof_type': SafetyEvidenceProof.ProofType.PHOTO, 'value': {'ok': True}},
            expected_code=201,
        )
        confirm_url = reverse(
            'repair-packet-gate-confirm', kwargs={'pk': packet.pk, 'gate_pk': gate.pk}
        )
        response = self.post(confirm_url, {'note': 'photo captured'}, expected_code=200)
        self.assertTrue(response.data['ok'])
        self.assertEqual(response.data['status'], GateStatus.CONFIRMED)

    def test_lockout_endpoint_blocks_close_until_restored(self):
        """Test lockout endpoint blocks close until restored."""
        packet = RepairPacket.objects.create(
            fault_summary='x', status=PacketStatus.EXECUTING
        )
        gate = RepairPacketGate.objects.create(
            packet=packet, name='LOTO', gate_type='loto', status=GateStatus.CONFIRMED
        )
        lockout_url = reverse(
            'repair-packet-gate-lockout', kwargs={'pk': packet.pk, 'gate_pk': gate.pk}
        )
        point = self.post(
            lockout_url,
            {
                'energy_source': LockoutPoint.EnergySource.ELECTRICAL,
                'isolation_device': 'MCC-1 bucket',
                'status': LockoutPoint.PointStatus.VERIFIED,
            },
            expected_code=200,
        )
        advance_url = reverse('repair-packet-advance', kwargs={'pk': packet.pk})
        blocked = self.post(advance_url, {'to': PacketStatus.CLOSED}, expected_code=400)
        self.assertFalse(blocked.data['ok'])
        self.assertIn('not restored', blocked.data['detail'])

        self.post(
            lockout_url,
            {
                'pk': point.data['pk'],
                'status': LockoutPoint.PointStatus.RESTORED,
                'isolation_device': 'MCC-1 bucket',
            },
            expected_code=200,
        )
        closed = self.post(advance_url, {'to': PacketStatus.CLOSED}, expected_code=200)
        self.assertTrue(closed.data['ok'])


class GeneratorSuggestedGateFloorTest(TestCase):
    """A generator-named safety step inherits its template's authority (S13).

    Suggested gates were persisted advisory (``is_blocking=False``) regardless
    of the deployment's templates, so a packet could display a LOTO gate that
    blocked nothing and a technician could advance straight past it. The
    template — not the model — decides whether an energy-control step blocks.
    """

    @classmethod
    def setUpTestData(cls):
        """One active blocking LOTO template that APPLIES to the test packets.

        Applicability matters: a template whose ``applies_to`` rules do not
        govern this packet must confer nothing (pinned in
        ``GateFloorReviewRegressionTest``), so the fixture has to match for the
        inheritance path to be exercised at all.
        """
        cls.template = SafetyGateTemplate.objects.create(
            name='Energy isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=True,
            is_mandatory=True,
            requires_second_person=True,
            default_sequence=3,
        )

    def _generate(self, packet, *, gate_type, name='AI suggested lockout'):
        from repair.generation import GeneratedSafetyGate, GenerationResult

        result = GenerationResult(
            diagnosis={},
            safety_gates=[GeneratedSafetyGate(name=name, gate_type=gate_type)],
        )
        return services._create_safety_gates(packet, result)

    def test_suggested_loto_gate_inherits_blocking_from_its_template(self):
        """The governing template's enforcement flags win over the default."""
        packet = RepairPacket.objects.create(
            fault_summary='Seal replacement on the drive', status=PacketStatus.DIAGNOSED
        )
        self._generate(packet, gate_type='loto')

        gate = packet.gates.get(name='AI suggested lockout')
        self.assertTrue(gate.is_blocking)
        self.assertTrue(gate.is_mandatory)
        self.assertTrue(gate.requires_second_person)
        # The template FK stays unset on a suggestion: it is the identity key
        # of the template-backed path, and claiming it collided (see
        # GateFloorReviewRegressionTest).
        self.assertIsNone(gate.template_id)

    def test_a_blocking_suggested_gate_actually_stops_approval(self):
        """The point of the flag: the packet cannot advance past it."""
        packet = RepairPacket.objects.create(
            fault_summary='Seal replacement on the drive', status=PacketStatus.DIAGNOSED
        )
        self._generate(packet, gate_type='loto')

        ok, detail = services.advance_packet(packet, PacketStatus.APPROVED)
        self.assertFalse(ok)
        self.assertIn('Safety gate', detail)

    def test_ungoverned_gate_type_stays_advisory(self):
        """An unrecognised suggestion must not invent blocking authority."""
        packet = RepairPacket.objects.create(
            fault_summary='Seal replacement on the drive', status=PacketStatus.DIAGNOSED
        )
        self._generate(packet, gate_type='other', name='Wear gloves')

        gate = packet.gates.get(name='Wear gloves')
        self.assertFalse(gate.is_blocking)
        self.assertFalse(gate.is_mandatory)
        self.assertIsNone(gate.template_id)

    def test_inactive_template_does_not_confer_authority(self):
        """A retired template governs nothing."""
        self.template.active = False
        self.template.save(update_fields=['active'])
        packet = RepairPacket.objects.create(
            fault_summary='Seal replacement on the drive', status=PacketStatus.DIAGNOSED
        )
        self._generate(packet, gate_type='loto')

        gate = packet.gates.get(name='AI suggested lockout')
        self.assertFalse(gate.is_blocking)


class GateFloorReviewRegressionTest(TestCase):
    """Defects an adversarial review of the S13 floor found before it shipped."""

    def _packet(self):
        return RepairPacket.objects.create(
            fault_summary='Seal replacement on the drive', status=PacketStatus.DIAGNOSED
        )

    def _run(self, packet, gates):
        from repair.generation import GeneratedSafetyGate, GenerationResult

        return services._create_safety_gates(
            packet,
            GenerationResult(
                diagnosis={},
                safety_gates=[
                    GeneratedSafetyGate(name=name, gate_type=gate_type)
                    for name, gate_type in gates
                ],
            ),
        )

    def test_two_suggestions_of_one_type_do_not_abort_generation(self):
        """The suggested gate must not claim the template-backed identity key.

        Writing ``template=T`` onto a suggestion made two suggestions of one
        type collapse onto ``(packet, template)`` — the key
        ``resolve_safety_gates`` does ``get_or_create`` on — raising
        MultipleObjectsReturned, which the caller's broad ``except`` turned
        into a rolled-back, failed generation run.
        """
        SafetyGateTemplate.objects.create(
            name='Energy isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=True,
            is_mandatory=True,
        )
        packet = self._packet()

        self._run(
            packet, [('Lock out the drive', 'loto'), ('Lock out the feed', 'loto')]
        )

        self.assertEqual(packet.gates.filter(is_blocking=True).count(), 3)

    def test_the_deployment_authored_template_gate_still_materialises(self):
        """A suggestion must not shadow the template's own name/instructions."""
        SafetyGateTemplate.objects.create(
            name='Energy isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=True,
        )
        packet = self._packet()

        self._run(packet, [('AI worded lockout', 'loto')])

        self.assertTrue(packet.gates.filter(name='Energy isolation').exists())
        self.assertTrue(packet.gates.filter(name='AI worded lockout').exists())

    def test_the_strictest_applicable_template_wins_not_the_lowest_sequence(self):
        """An advisory template must not shadow a blocking one of the same type."""
        SafetyGateTemplate.objects.create(
            name='Advisory check',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=False,
            is_mandatory=False,
            default_sequence=0,
        )
        SafetyGateTemplate.objects.create(
            name='Hard isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=True,
            is_mandatory=True,
            default_sequence=9,
        )
        packet = self._packet()

        self._run(packet, [('AI worded lockout', 'loto')])

        self.assertTrue(packet.gates.get(name='AI worded lockout').is_blocking)

    def test_an_inapplicable_template_confers_nothing(self):
        """applies_to governs the floor exactly as it governs the template path."""
        SafetyGateTemplate.objects.create(
            name='Boiler isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['boiler']},
            is_blocking=True,
        )
        packet = self._packet()

        self._run(packet, [('AI worded lockout', 'loto')])

        self.assertFalse(packet.gates.get(name='AI worded lockout').is_blocking)

    def test_regeneration_upgrades_a_gate_persisted_advisory(self):
        """defaults= applies only on INSERT, so an old gate stayed advisory."""
        packet = self._packet()
        RepairPacketGate.objects.create(
            packet=packet,
            name='AI worded lockout',
            gate_type='loto',
            is_blocking=False,
            is_mandatory=False,
        )
        SafetyGateTemplate.objects.create(
            name='Energy isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=True,
            is_mandatory=True,
            requires_second_person=True,
        )

        self._run(packet, [('AI worded lockout', 'loto')])

        gate = packet.gates.get(name='AI worded lockout')
        self.assertTrue(gate.is_blocking)
        self.assertTrue(gate.is_mandatory)
        self.assertTrue(gate.requires_second_person)

    def test_template_and_suggestion_may_share_a_name_across_regeneration(self):
        """Suggestion identity must exclude the template-backed sibling.

        The authored row and AI row legitimately share a display name. Looking
        up a suggestion by ``(packet, name)`` alone finds both rows on the next
        generation and raises ``MultipleObjectsReturned``, failing the entire
        packet generation run.
        """
        SafetyGateTemplate.objects.create(
            name='Energy isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=True,
        )
        packet = self._packet()

        self._run(packet, [('Energy isolation', 'loto')])
        self._run(packet, [('Energy isolation', 'loto')])

        rows = packet.gates.filter(name='Energy isolation')
        self.assertEqual(rows.count(), 2)
        self.assertEqual(rows.filter(template__isnull=True).count(), 1)
        self.assertEqual(rows.filter(template__isnull=False).count(), 1)

    def test_generated_gate_type_is_canonical_before_it_is_persisted(self):
        """Formatting noise must not disable the exact LOTO interlocks.

        Model output such as ``"  LOTO "`` matched the template but was stored
        verbatim. Both confirmation and return-to-service then ignored the
        gate's physical lockout points because those checks compare to the
        canonical ``loto`` value.
        """
        SafetyGateTemplate.objects.create(
            name='Energy isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=True,
        )
        packet = self._packet()

        self._run(packet, [('AI worded lockout', '  LOTO ')])
        gate = packet.gates.get(name='AI worded lockout')
        template_gate = packet.gates.get(template__isnull=False)
        template_ok, template_detail = services.confirm_gate(template_gate)
        self.assertTrue(template_ok, template_detail)
        LockoutPoint.objects.create(
            gate=gate,
            energy_source=LockoutPoint.EnergySource.ELECTRICAL,
            isolation_device='MCC-1 bucket',
        )

        self.assertEqual(gate.gate_type, 'loto')
        confirm_ok, confirm_detail = services.confirm_gate(gate)
        close_ok, close_detail = packet.can_return_to_service()
        self.assertFalse(confirm_ok, confirm_detail)
        self.assertFalse(close_ok, close_detail)

    def test_regeneration_upgrades_other_to_known_type_without_downgrade(self):
        """A known type may harden ``other`` but weak output cannot erase it.

        Same-name regeneration previously left an old ``other`` value in place,
        so the row gained blocking flags without gaining the exact LOTO
        interlocks. Conversely, a later unrecognised output must not turn a
        known LOTO row back into ``other``.
        """
        packet = self._packet()
        RepairPacketGate.objects.create(
            packet=packet,
            name='AI worded lockout',
            gate_type='other',
            is_blocking=False,
            is_mandatory=False,
        )
        SafetyGateTemplate.objects.create(
            name='Energy isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=True,
        )

        self._run(packet, [('AI worded lockout', ' LOTO ')])
        gate = packet.gates.get(name='AI worded lockout')
        self.assertEqual(gate.gate_type, 'loto')

        self._run(packet, [('AI worded lockout', 'unrecognised')])
        gate.refresh_from_db()
        self.assertEqual(gate.gate_type, 'loto')

    def test_regeneration_upgrades_each_safety_dimension_independently(self):
        """An already-blocking row can still be weak on every other dimension.

        Gating the upgrade on ``not row.is_blocking`` left mandatory, photo,
        permission and second-person requirements unset forever on partially
        upgraded rows.
        """
        packet = self._packet()
        RepairPacketGate.objects.create(
            packet=packet,
            name='AI worded lockout',
            gate_type='loto',
            is_blocking=True,
            is_mandatory=False,
            requires_photo=False,
            requires_second_person=False,
        )
        SafetyGateTemplate.objects.create(
            name='Energy isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=True,
            is_mandatory=True,
            required_permission='repair.change_repairpacket',
            requires_photo=True,
            requires_second_person=True,
            default_sequence=17,
        )

        self._run(packet, [('AI worded lockout', 'loto')])

        gate = packet.gates.get(name='AI worded lockout')
        self.assertTrue(gate.is_blocking)
        self.assertTrue(gate.is_mandatory)
        self.assertTrue(gate.requires_photo)
        self.assertTrue(gate.requires_second_person)
        self.assertEqual(gate.required_permission, 'repair.change_repairpacket')
        self.assertEqual(gate.sequence, 17)

    def test_existing_template_row_adopts_only_stricter_template_edits(self):
        """Editing a deployed template must harden packets already resolved.

        ``get_or_create(defaults=...)`` applies the template contract only on
        insert. Without an update path, an existing packet remains advisory
        after its governing template is made blocking.
        """
        template = SafetyGateTemplate.objects.create(
            name='Energy isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=False,
            is_mandatory=False,
        )
        packet = self._packet()
        services.resolve_safety_gates(packet)

        template.is_blocking = True
        template.is_mandatory = True
        template.required_permission = 'repair.change_repairpacket'
        template.requires_photo = True
        template.requires_second_person = True
        template.save()
        services.resolve_safety_gates(packet)

        gate = packet.gates.get(template=template)
        self.assertTrue(gate.is_blocking)
        self.assertTrue(gate.is_mandatory)
        self.assertTrue(gate.requires_photo)
        self.assertTrue(gate.requires_second_person)
        self.assertEqual(gate.required_permission, 'repair.change_repairpacket')

        template.is_blocking = False
        template.is_mandatory = False
        template.required_permission = ''
        template.requires_photo = False
        template.requires_second_person = False
        template.save()
        services.resolve_safety_gates(packet)

        gate.refresh_from_db()
        self.assertTrue(gate.is_blocking)
        self.assertTrue(gate.is_mandatory)
        self.assertTrue(gate.requires_photo)
        self.assertTrue(gate.requires_second_person)
        self.assertEqual(gate.required_permission, 'repair.change_repairpacket')

    def test_same_type_templates_merge_incomparable_safety_requirements(self):
        """No single sort order can represent independent safety dimensions.

        One applicable template can require a photo while another requires a
        second person and permission. The generated row must inherit the union,
        while its order follows the strict governing template rather than the
        model-row default of zero.
        """
        SafetyGateTemplate.objects.create(
            name='Photograph isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=True,
            is_mandatory=True,
            requires_photo=True,
            default_sequence=41,
        )
        SafetyGateTemplate.objects.create(
            name='Witness isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=True,
            is_mandatory=True,
            required_permission='repair.change_repairpacket',
            requires_second_person=True,
            default_sequence=73,
        )
        packet = self._packet()

        self._run(packet, [('AI worded lockout', 'loto')])

        gate = packet.gates.get(name='AI worded lockout')
        self.assertTrue(gate.requires_photo)
        self.assertTrue(gate.requires_second_person)
        self.assertEqual(gate.required_permission, 'repair.change_repairpacket')
        self.assertEqual(gate.sequence, 73)

    def test_gate_type_is_normalised_before_matching(self):
        """The model's spelling must not decide whether the floor applies."""
        SafetyGateTemplate.objects.create(
            name='Energy isolation',
            gate_type='loto',
            applies_to={'fault_keywords': ['seal']},
            is_blocking=True,
        )
        packet = self._packet()

        self._run(packet, [('AI worded lockout', '  LOTO ')])

        self.assertTrue(packet.gates.get(name='AI worded lockout').is_blocking)
