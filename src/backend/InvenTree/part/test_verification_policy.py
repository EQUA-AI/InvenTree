"""Golden deterministic compatibility cases for the Right-Part Finder.

Spec section 20.2 subset: exercises requirement construction
(``build_requirements``), hard-rule evaluation (``evaluate_candidate``), and
tiered retrieval through the service layer with a fixed golden policy of a
hard voltage envelope (range_within 440..480 V), a hard phase equality, and
one soft ranked attribute.
"""

import itertools
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings

from common.models import Parameter, ParameterTemplate
from part.models import BomItem, BomItemSubstitute, Part, PartRelated
from part.verification import services
from part.verification.errors import (
    VerificationNoSafeMatchInvalid,
    VerificationUseError,
)
from part.verification.policy import create_policy_version, revoke_policy
from part.verification.schema import BlockerCodes, ConsumerCodes, DecisionKind
from part.verification.scope import VerificationScope
from part.verification_models import PartVerificationUse

# Explicit global scope used by every golden case
GLOBAL_SCOPE = VerificationScope(customer_id=None, site_key=None)

# Rank factor identifiers declared by the golden policy (closed set)
DECLARED_RANK_FACTORS = {
    'exact_requested_identity',
    'evidence_coverage',
    'preferred_representation',
    'freshness',
}


@override_settings(
    AIMMS_RPF_ENABLED=True,
    AIMMS_RPF_COLLECTION_ENABLED=True,
    AIMMS_RPF_EVALUATION_ENABLED=True,
    AIMMS_RPF_CONFIRMATION_ENABLED=True,
)
class VerificationPolicyGoldenTests(TestCase):
    """Golden deterministic compatibility cases (spec section 20.2 subset)."""

    @classmethod
    def setUpTestData(cls):
        """Create the shared actor and globally-unique parameter templates."""
        cls.user = get_user_model().objects.create_superuser(
            username='rpf_golden_admin',
            email='rpf_golden_admin@example.com',
            password='x',
        )
        cls.part_ct = ContentType.objects.get_for_model(Part)
        cls.volt_template = ParameterTemplate.objects.create(
            name='PVGoldenVoltage', units='V'
        )
        cls.phase_template = ParameterTemplate.objects.create(
            name='PVGoldenPhase', units=''
        )
        cls.frame_template = ParameterTemplate.objects.create(
            name='PVGoldenFrame', units=''
        )
        cls.volt_mv_template = ParameterTemplate.objects.create(
            name='PVGoldenVoltageMilli', units='mV'
        )
        cls.volt_bar_template = ParameterTemplate.objects.create(
            name='PVGoldenVoltageBar', units='bar'
        )

    def setUp(self):
        """Attach the global verification scope to the superuser actor."""
        super().setUp()
        self.actor = self.user
        self.actor.verification_scopes = {GLOBAL_SCOPE}
        self._key_counter = itertools.count(1)
        # Idempotency keys are capped at 64 characters; use a short namespace
        self._key_namespace = uuid.uuid4().hex[:12]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _key(self) -> str:
        """Return a fresh idempotency key for one command."""
        return f'golden-{self._key_namespace}-{next(self._key_counter)}'

    def _policy_definition(
        self,
        *,
        voltage_candidate_template: str | None = None,
        candidate_missing: str = 'exclude',
        max_candidates: int = 50,
    ) -> dict:
        """Build the golden policy document, optionally varied per case."""
        voltage = {
            'key': 'electrical.voltage',
            'category': 'electrical',
            'value_kind': 'range',
            'operator': 'range_within',
            'unit': 'V',
            'hard': True,
            'sources': [
                {'kind': 'observation'},
                {'kind': 'parameter', 'template': self.volt_template.name},
            ],
            'missing_blocker': BlockerCodes.NAMEPLATE_REQUIRED,
            'conflict_code': BlockerCodes.VOLTAGE_CONFLICT,
        }
        if voltage_candidate_template is not None:
            voltage['candidate_sources'] = [
                {'kind': 'parameter', 'template': voltage_candidate_template}
            ]
        phase = {
            'key': 'electrical.phase',
            'category': 'electrical',
            'value_kind': 'decimal',
            'operator': 'eq',
            'unit': '',
            'hard': True,
            'sources': [{'kind': 'parameter', 'template': self.phase_template.name}],
            'candidate_missing': candidate_missing,
            'conflict_code': BlockerCodes.PHASE_CONFLICT,
        }
        frame = {
            'key': 'general.frame',
            'category': 'general',
            'value_kind': 'text',
            'operator': 'eq',
            'hard': False,
            'sources': [{'kind': 'parameter', 'template': self.frame_template.name}],
        }
        return {
            'schema_version': 1,
            'description': 'RPF golden compatibility policy',
            'requirements': [voltage, phase, frame],
            'retrieval': {'max_candidates': max_candidates, 'tier_cap': 25},
            'rank_factors': [
                {'id': 'exact_requested_identity', 'max': 25},
                {'id': 'evidence_coverage', 'max': 15},
                {'id': 'preferred_representation', 'max': 4},
                {'id': 'freshness', 'max': 3},
            ],
            'revalidation': {
                'non_material_paths': ['requested_part.description'],
                'expiry_hours': 24,
            },
        }

    def _activate_policy(self, **overrides):
        """Create and activate the golden policy as the configured version."""
        return create_policy_version(
            key='rpf-core',
            version=1,
            definition=self._policy_definition(**overrides),
            activate=True,
        )

    def _part(self, name: str, ipn: str = '') -> Part:
        """Create one active component part."""
        return Part.objects.create(name=name, IPN=ipn, active=True, component=True)

    def _set_parameter(self, part: Part, template, value: str):
        """Attach one generic parameter row to a part."""
        Parameter.objects.create(
            model_type=self.part_ct, model_id=part.pk, template=template, data=value
        )

    def _electrical(self, part: Part, *, volts='460', phase='3', frame='F100'):
        """Attach the standard electrical parameters; None skips a value."""
        if volts is not None:
            self._set_parameter(part, self.volt_template, volts)
        if phase is not None:
            self._set_parameter(part, self.phase_template, phase)
        if frame is not None:
            self._set_parameter(part, self.frame_template, frame)

    def _session(self, requested: Part):
        """Create one manual-purpose session for a requested part."""
        return services.create_session(
            purpose='manual',
            actor=self.actor,
            idempotency_key=self._key(),
            requested_part_id=requested.pk,
        )

    def _accept_voltage_envelope(self, session, minimum='440', maximum='480'):
        """Attach and accept the observed voltage application envelope."""
        evidence = services.attach_evidence(
            session_id=session.pk,
            actor=self.actor,
            idempotency_key=self._key(),
            requirement_key='electrical.voltage',
            value={'min': minimum, 'max': maximum},
            unit='V',
        )
        services.decide_evidence(
            session_id=session.pk,
            evidence_id=evidence.pk,
            actor=self.actor,
            idempotency_key=self._key(),
            accept=True,
        )
        return evidence

    def _evaluate(self, session, expected_revision=1):
        """Evaluate the session through the service layer."""
        return services.evaluate_session(
            session_id=session.pk,
            actor=self.actor,
            expected_revision=expected_revision,
            idempotency_key=self._key(),
        )

    def _evaluations(self, session) -> dict:
        """Return current-revision evaluations keyed by candidate part pk."""
        session.refresh_from_db()
        return {
            row.candidate_id: row
            for row in session.candidate_evaluations.filter(
                session_revision=session.revision
            )
        }

    @staticmethod
    def _codes(entries: list) -> list:
        """Return the stable reason codes from attribute-result records."""
        return [entry['reason_code'] for entry in entries]

    # ------------------------------------------------------------------
    # Golden cases
    # ------------------------------------------------------------------

    def test_exact_match_is_eligible_with_visible_rank_factors(self):
        """Case 1: an exact voltage/phase match is eligible and fully explained."""
        self._activate_policy()
        requested = self._part('Golden Requested 1', 'GOLD-R1')
        candidate = self._part('Golden Candidate 1', 'GOLD-C1')
        PartRelated.objects.create(part_1=requested, part_2=candidate)
        self._electrical(requested)
        self._electrical(candidate)

        session = self._session(requested)
        self._accept_voltage_envelope(session)
        result = self._evaluate(session)

        self.assertEqual(result['state'], 'review_required')
        rows = self._evaluations(session)
        row = rows[candidate.pk]

        self.assertTrue(row.eligible)
        self.assertIsNotNone(row.rank)
        self.assertEqual(row.hard_conflicts, [])
        matched_keys = [entry['key'] for entry in row.matched_attributes]
        self.assertIn('electrical.voltage', matched_keys)
        self.assertIn('electrical.phase', matched_keys)

        # Every rank factor is individually visible and explainable
        self.assertTrue(row.rank_factors)
        for factor in row.rank_factors:
            self.assertEqual(set(factor), {'id', 'contribution', 'max', 'reason'})
        self.assertEqual(
            row.rank_value, sum(factor['contribution'] for factor in row.rank_factors)
        )
        coverage = {f['id']: f for f in row.rank_factors}['evidence_coverage']
        self.assertEqual(coverage['contribution'], 15)

        # The exact requested part deterministically outranks the related match
        self.assertEqual(rows[requested.pk].rank, 1)
        self.assertEqual(row.rank, 2)

    def test_wrong_phase_is_excluded_and_never_ranked(self):
        """Case 2: correct voltage but wrong phase excludes; no rank is given."""
        self._activate_policy()
        requested = self._part('Golden Requested 2', 'GOLD-R2')
        candidate = self._part('Golden Candidate 2', 'GOLD-C2')
        PartRelated.objects.create(part_1=requested, part_2=candidate)
        self._electrical(requested)
        self._electrical(candidate, phase='1')

        session = self._session(requested)
        self._accept_voltage_envelope(session)
        self._evaluate(session)

        row = self._evaluations(session)[candidate.pk]
        self.assertFalse(row.eligible)
        self.assertIn(BlockerCodes.PHASE_CONFLICT, self._codes(row.hard_conflicts))
        self.assertIsNone(row.rank)
        self.assertIsNone(row.rank_value)
        self.assertEqual(row.rank_factors, [])

    def test_missing_voltage_everywhere_blocks_with_nameplate_required(self):
        """Case 3: a missing hard requirement fact blocks before any evaluation."""
        self._activate_policy()
        requested = self._part('Golden Requested 3', 'GOLD-R3')
        # Phase and frame exist; the voltage has no parameter and no observation
        self._electrical(requested, volts=None)
        candidate = self._part('Golden Candidate 3', 'GOLD-C3')
        PartRelated.objects.create(part_1=requested, part_2=candidate)
        self._electrical(candidate)

        session = self._session(requested)
        result = self._evaluate(session)

        self.assertEqual(result['state'], 'collecting')
        self.assertEqual(
            [blocker['code'] for blocker in result['blockers']],
            [BlockerCodes.NAMEPLATE_REQUIRED],
        )
        self.assertEqual(result['blockers'][0]['attribute'], 'electrical.voltage')

        session.refresh_from_db()
        self.assertEqual(session.state, 'collecting')
        # Nothing was evaluated: no candidate rows exist at all
        self.assertEqual(session.candidate_evaluations.count(), 0)

    def test_candidate_missing_phase_exclude_behavior(self):
        """Case 4a: a candidate without the required phase parameter is excluded."""
        self._activate_policy(candidate_missing='exclude')
        requested = self._part('Golden Requested 4A', 'GOLD-R4A')
        candidate = self._part('Golden Candidate 4A', 'GOLD-C4A')
        PartRelated.objects.create(part_1=requested, part_2=candidate)
        self._electrical(requested)
        self._electrical(candidate, phase=None)

        session = self._session(requested)
        self._accept_voltage_envelope(session)
        self._evaluate(session)

        row = self._evaluations(session)[candidate.pk]
        self.assertFalse(row.eligible)
        self.assertIsNone(row.rank)
        entry = next(
            item for item in row.missing_attributes if item['key'] == 'electrical.phase'
        )
        self.assertEqual(entry['outcome'], 'missing')
        self.assertEqual(entry['reason_code'], BlockerCodes.CANDIDATE_ATTRIBUTE_MISSING)

    def test_candidate_missing_phase_indeterminate_behavior(self):
        """Case 4b: indeterminate handling is still never an eligibility pass."""
        self._activate_policy(candidate_missing='indeterminate')
        requested = self._part('Golden Requested 4B', 'GOLD-R4B')
        candidate = self._part('Golden Candidate 4B', 'GOLD-C4B')
        PartRelated.objects.create(part_1=requested, part_2=candidate)
        self._electrical(requested)
        self._electrical(candidate, phase=None)

        session = self._session(requested)
        self._accept_voltage_envelope(session)
        self._evaluate(session)

        row = self._evaluations(session)[candidate.pk]
        self.assertFalse(row.eligible)
        self.assertIsNone(row.rank)
        entry = next(
            item for item in row.missing_attributes if item['key'] == 'electrical.phase'
        )
        self.assertEqual(entry['outcome'], 'indeterminate')
        self.assertEqual(entry['reason_code'], BlockerCodes.CANDIDATE_ATTRIBUTE_MISSING)

    def test_unit_conversion_equality_millivolts(self):
        """Case 5: a numerically equal mV candidate value passes the V envelope."""
        self._activate_policy(voltage_candidate_template=self.volt_mv_template.name)
        requested = self._part('Golden Requested 5', 'GOLD-R5')
        candidate = self._part('Golden Candidate 5', 'GOLD-C5')
        PartRelated.objects.create(part_1=requested, part_2=candidate)
        self._electrical(requested)
        self._set_parameter(candidate, self.volt_mv_template, '460000')
        self._set_parameter(candidate, self.phase_template, '3')
        self._set_parameter(candidate, self.frame_template, 'F100')

        session = self._session(requested)
        self._accept_voltage_envelope(session)
        self._evaluate(session)

        row = self._evaluations(session)[candidate.pk]
        self.assertTrue(row.eligible)
        entry = next(
            item
            for item in row.matched_attributes
            if item['key'] == 'electrical.voltage'
        )
        # The raw candidate value is preserved beside the canonical volts
        self.assertEqual(entry['candidate']['raw'], '460000')
        self.assertEqual(Decimal(entry['candidate']['value']['min']), Decimal('460'))
        self.assertEqual(Decimal(entry['candidate']['value']['max']), Decimal('460'))

        # Raw is preserved on the persisted requirement row too
        session.refresh_from_db()
        requirement = session.requirements.get(key='electrical.voltage')
        self.assertEqual(requirement.resolution, 'accepted')
        self.assertEqual(requirement.raw_value, {'min': '440', 'max': '480'})
        self.assertEqual(requirement.value, {'min': '440.000000', 'max': '480.000000'})

    def test_incompatible_dimension_is_indeterminate_never_pass(self):
        """Case 6: the same numeric text in 'bar' can never pass a volt rule."""
        self._activate_policy(voltage_candidate_template=self.volt_bar_template.name)
        requested = self._part('Golden Requested 6', 'GOLD-R6')
        candidate = self._part('Golden Candidate 6', 'GOLD-C6')
        PartRelated.objects.create(part_1=requested, part_2=candidate)
        self._electrical(requested)
        self._set_parameter(candidate, self.volt_bar_template, '460')
        self._set_parameter(candidate, self.phase_template, '3')

        session = self._session(requested)
        self._accept_voltage_envelope(session)
        self._evaluate(session)

        row = self._evaluations(session)[candidate.pk]
        self.assertFalse(row.eligible)
        self.assertIsNone(row.rank)
        matched_keys = [item['key'] for item in row.matched_attributes]
        self.assertNotIn('electrical.voltage', matched_keys)
        entry = next(
            item
            for item in row.missing_attributes
            if item['key'] == 'electrical.voltage'
        )
        self.assertEqual(entry['outcome'], 'indeterminate')
        self.assertEqual(entry['reason_code'], BlockerCodes.UNIT_DIMENSION_MISMATCH)

    def test_inclusive_envelope_boundaries(self):
        """Case 7: exact envelope bounds pass; just outside always fails."""
        self._activate_policy()
        requested = self._part('Golden Requested 7', 'GOLD-R7')
        self._electrical(requested)

        cases = [
            ('Golden Edge Low', 'GOLD-E1', '440', True),
            ('Golden Edge High', 'GOLD-E2', '480', True),
            ('Golden Below', 'GOLD-E3', '439.999999', False),
            ('Golden Above', 'GOLD-E4', '480.000001', False),
        ]
        parts = {}
        for name, ipn, volts, _expected in cases:
            part = self._part(name, ipn)
            PartRelated.objects.create(part_1=requested, part_2=part)
            self._electrical(part, volts=volts)
            parts[ipn] = part

        session = self._session(requested)
        self._accept_voltage_envelope(session)
        self._evaluate(session)

        rows = self._evaluations(session)
        for _name, ipn, volts, expected in cases:
            row = rows[parts[ipn].pk]
            self.assertEqual(
                row.eligible,
                expected,
                f'candidate at {volts} V expected eligible={expected}',
            )
            if not expected:
                self.assertIn(
                    BlockerCodes.VOLTAGE_CONFLICT, self._codes(row.hard_conflicts)
                )
                self.assertIsNone(row.rank)

    def test_leading_zero_ipn_not_collapsed_by_retrieval(self):
        """Case 8: IPN '007' and IPN '7' remain distinct identifier universes."""
        self._activate_policy()
        requested = self._part('Golden Requested 007', '007')
        twin = self._part('Golden Twin 007', '007')
        other = self._part('Golden Plain 7', '7')
        for part in (requested, twin, other):
            self._electrical(part)

        session = self._session(requested)
        self._accept_voltage_envelope(session)
        self._evaluate(session)

        rows = self._evaluations(session)
        self.assertIn(twin.pk, rows)
        self.assertIn('ipn', rows[twin.pk].retrieval_tiers)
        # Leading zeros are significant: IPN '7' is never pulled in for '007'
        self.assertNotIn(other.pk, rows)

    def test_bom_substitute_tier_requires_hard_facts(self):
        """Case 9: line substitutes are retrieved but never bypass hard rules."""
        self._activate_policy()
        component = self._part('Golden BOM Component', 'GOLD-B1')
        self._electrical(component)
        assembly = Part.objects.create(
            name='Golden Assembly', assembly=True, active=True
        )
        sub_ok = self._part('Golden Substitute OK', 'GOLD-S1')
        self._electrical(sub_ok)
        sub_bad = self._part('Golden Substitute Bad', 'GOLD-S2')
        self._electrical(sub_bad, phase='1')
        # MPTT root insertion orders trees by name and may renumber earlier
        # rows; refresh so the recursion check compares current tree ids
        for part in (component, assembly, sub_ok, sub_bad):
            part.refresh_from_db()
        bom_item = BomItem.objects.create(part=assembly, sub_part=component, quantity=1)
        BomItemSubstitute.objects.create(bom_item=bom_item, part=sub_ok)
        BomItemSubstitute.objects.create(bom_item=bom_item, part=sub_bad)

        session = services.create_session(
            purpose='bom_component',
            actor=self.actor,
            idempotency_key=self._key(),
            bom_item_id=bom_item.pk,
        )
        self._accept_voltage_envelope(session)
        self._evaluate(session)

        rows = self._evaluations(session)
        self.assertIn('bom_substitute', rows[sub_ok.pk].retrieval_tiers)
        self.assertTrue(rows[sub_ok.pk].eligible)

        # The substitute relation alone never makes an incompatible part pass
        self.assertIn('bom_substitute', rows[sub_bad.pk].retrieval_tiers)
        self.assertFalse(rows[sub_bad.pk].eligible)
        self.assertIn(
            BlockerCodes.PHASE_CONFLICT, self._codes(rows[sub_bad.pk].hard_conflicts)
        )
        self.assertIsNone(rows[sub_bad.pk].rank)

    def test_related_stock_never_changes_eligibility_or_rank(self):
        """Case 10: availability is advisory; stock buys no eligibility or rank."""
        from stock.models import StockItem

        self._activate_policy()
        requested = self._part('Golden Requested 10', 'GOLD-R10')
        self._electrical(requested)

        stocked = self._part('Golden Stocked Related', 'GOLD-T1')
        plain = self._part('Golden Plain Related', 'GOLD-T2')
        bad = self._part('Golden Bad Related', 'GOLD-T3')
        for part in (stocked, plain, bad):
            PartRelated.objects.create(part_1=requested, part_2=part)
        self._electrical(stocked)
        self._electrical(plain)
        self._electrical(bad, phase='1')
        StockItem.objects.create(part=stocked, quantity=500)
        StockItem.objects.create(part=bad, quantity=500)

        session = self._session(requested)
        self._accept_voltage_envelope(session)
        self._evaluate(session)

        rows = self._evaluations(session)
        self.assertIn('related', rows[stocked.pk].retrieval_tiers)
        self.assertTrue(rows[stocked.pk].eligible)
        self.assertTrue(rows[plain.pk].eligible)

        # Identical facts rank identically regardless of stock; ties go by pk
        self.assertEqual(rows[stocked.pk].rank_value, rows[plain.pk].rank_value)
        self.assertLess(rows[stocked.pk].rank, rows[plain.pk].rank)
        for row in (rows[stocked.pk], rows[plain.pk]):
            factor_ids = {factor['id'] for factor in row.rank_factors}
            self.assertEqual(factor_ids, DECLARED_RANK_FACTORS)
            self.assertFalse(
                any('availability' in fid or 'stock' in fid for fid in factor_ids)
            )

        # Rich stock never restores an incompatible related part
        self.assertFalse(rows[bad.pk].eligible)
        self.assertIsNone(rows[bad.pk].rank)
        self.assertEqual(rows[bad.pk].rank_factors, [])

        # The availability snapshot is recorded, but only as advisory data
        snapshot = rows[stocked.pk].availability_snapshot
        self.assertEqual(Decimal(snapshot['in_stock']), Decimal(500))
        self.assertIn('advisory', snapshot['caveat'])

    def test_no_safe_match_when_universe_complete_and_all_excluded(self):
        """Case 11: all excluded with a complete universe permits no-safe-match."""
        self._activate_policy()
        requested = self._part('Golden Requested 11', 'GOLD-R11')
        # The requested part itself fails the observed envelope as a candidate
        self._electrical(requested, volts='400')
        other = self._part('Golden Candidate 11', 'GOLD-C11')
        PartRelated.objects.create(part_1=requested, part_2=other)
        self._electrical(other, volts='430')

        session = self._session(requested)
        self._accept_voltage_envelope(session)
        result = self._evaluate(session)

        self.assertTrue(result['universe_complete'])
        self.assertEqual(result['eligible'], 0)
        rows = self._evaluations(session)
        self.assertTrue(rows)
        self.assertFalse(any(row.eligible for row in rows.values()))

        decision = services.mark_no_safe_match(
            session_id=session.pk,
            actor=self.actor,
            expected_revision=1,
            idempotency_key=self._key(),
            reason='no candidate satisfies the envelope',
        )
        self.assertEqual(decision.kind, DecisionKind.NO_SAFE_MATCH)
        self.assertIsNone(decision.selected_part_id)
        self.assertIsNone(decision.selected_evaluation_id)
        session.refresh_from_db()
        self.assertEqual(session.state, 'no_safe_match')

    def test_search_limit_blocks_no_safe_match(self):
        """Case 12: a capped universe forbids an exhaustive no-match claim."""
        self._activate_policy(max_candidates=2)
        requested = self._part('Golden Requested 12', 'GOLD-R12')
        self._electrical(requested, volts='400')
        for index in range(3):
            part = self._part(f'Golden Capped {index}', f'GOLD-K{index}')
            PartRelated.objects.create(part_1=requested, part_2=part)
            self._electrical(part, volts='400')

        session = self._session(requested)
        self._accept_voltage_envelope(session)
        result = self._evaluate(session)

        self.assertFalse(result['universe_complete'])
        session.refresh_from_db()
        self.assertFalse(session.universe_complete)
        self.assertEqual(len(self._evaluations(session)), 2)

        with self.assertRaises(VerificationNoSafeMatchInvalid) as context:
            services.mark_no_safe_match(
                session_id=session.pk,
                actor=self.actor,
                expected_revision=1,
                idempotency_key=self._key(),
                reason='attempted exhaustive claim on a capped universe',
            )
        self.assertIn(
            BlockerCodes.SEARCH_LIMIT_REACHED,
            [blocker['code'] for blocker in context.exception.blockers],
        )

    def test_determinism_identical_facts_identical_hashes(self):
        """Case 13: identical facts yield identical hashes and ordering."""
        self._activate_policy()
        requested = self._part('Golden Requested 13', 'GOLD-R13')
        self._electrical(requested)
        good_a = self._part('Golden Det Good A', 'GOLD-D1')
        good_b = self._part('Golden Det Good B', 'GOLD-D2')
        excluded = self._part('Golden Det Excluded', 'GOLD-D3')
        for part in (good_a, good_b, excluded):
            PartRelated.objects.create(part_1=requested, part_2=part)
        self._electrical(good_a)
        self._electrical(good_b)
        self._electrical(excluded, phase='1')

        session_a = self._session(requested)
        self._accept_voltage_envelope(session_a)
        self._evaluate(session_a)

        session_b = self._session(requested)
        self._accept_voltage_envelope(session_b)
        self._evaluate(session_b)

        rows_a = self._evaluations(session_a)
        rows_b = self._evaluations(session_b)

        self.assertEqual(session_a.requirements_hash, session_b.requirements_hash)
        self.assertEqual(session_a.evaluation_hash, session_b.evaluation_hash)

        # Identical candidate universes with identical per-candidate hashes
        self.assertEqual(set(rows_a), set(rows_b))
        for candidate_pk, row in rows_a.items():
            self.assertEqual(row.evaluation_hash, rows_b[candidate_pk].evaluation_hash)

        # Identical deterministic survivor ordering
        order_a = list(
            session_a.candidate_evaluations
            .filter(session_revision=session_a.revision, eligible=True)
            .order_by('rank')
            .values_list('candidate_id', flat=True)
        )
        order_b = list(
            session_b.candidate_evaluations
            .filter(session_revision=session_b.revision, eligible=True)
            .order_by('rank')
            .values_list('candidate_id', flat=True)
        )
        self.assertEqual(order_a, order_b)
        self.assertEqual(order_a[0], requested.pk)

    def test_conflicting_candidate_facts_block_fail_closed(self):
        """Case 15: contradictory candidate facts never pass via one match.

        Spec section 8.3: equal-authority contradictions block. When a policy
        declares two candidate sources for one attribute and they disagree, a
        matching value from one source must not mask the conflicting value
        from the other; the candidate fails closed with EVIDENCE_CONFLICT.
        """
        alt_phase = ParameterTemplate.objects.create(name='PVGoldenPhaseAlt', units='')
        definition = self._policy_definition()
        for entry in definition['requirements']:
            if entry['key'] == 'electrical.phase':
                entry['candidate_sources'] = [
                    {'kind': 'parameter', 'template': self.phase_template.name},
                    {'kind': 'parameter', 'template': alt_phase.name},
                ]
        create_policy_version(
            key='rpf-core', version=1, definition=definition, activate=True
        )

        requested = self._part('Golden Requested 15', 'GOLD-R15')
        candidate = self._part('Golden Candidate 15', 'GOLD-C15')
        PartRelated.objects.create(part_1=requested, part_2=candidate)
        self._electrical(requested)
        self._electrical(candidate)
        # A second, contradictory phase fact on the alternate template: the
        # primary template's matching '3' must not mask this '1'.
        self._set_parameter(candidate, alt_phase, '1')

        session = self._session(requested)
        self._evaluate(session)
        rows = self._evaluations(session)

        row = rows[candidate.pk]
        self.assertFalse(row.eligible)
        self.assertIsNone(row.rank)
        codes = [item['reason_code'] for item in row.missing_attributes]
        self.assertIn(BlockerCodes.EVIDENCE_CONFLICT, codes)

    def test_policy_revoked_after_confirmation_blocks_use(self):
        """Case 14: revoking the bound policy makes the decision unusable."""
        self._activate_policy()
        requested = self._part('Golden Requested 14', 'GOLD-R14')
        candidate = self._part('Golden Candidate 14', 'GOLD-C14')
        PartRelated.objects.create(part_1=requested, part_2=candidate)
        self._electrical(requested)
        self._electrical(candidate)

        session = self._session(requested)
        self._accept_voltage_envelope(session)
        self._evaluate(session)

        rows = self._evaluations(session)
        decision = services.confirm_candidate(
            session_id=session.pk,
            evaluation_id=rows[candidate.pk].pk,
            actor=self.actor,
            expected_revision=1,
            idempotency_key=self._key(),
            reason='golden confirmation',
        )
        session.refresh_from_db()
        self.assertEqual(session.state, 'confirmed')

        revoke_policy(session.policy)

        with self.assertRaises(VerificationUseError) as context:
            services.validate_and_bind_use(
                decision_id=decision.pk,
                actor=self.actor,
                consumer_kind='job_kit',
                consumer_action='substitution_decide',
                idempotency_key=self._key(),
                expected_requested_part_id=requested.pk,
                expected_selected_part_id=candidate.pk,
                command_hash='sha256:golden',
            )
        self.assertEqual(
            context.exception.code, ConsumerCodes.PART_VERIFICATION_POLICY_INVALID
        )
        self.assertEqual(
            PartVerificationUse.objects.filter(decision=decision).count(), 0
        )
