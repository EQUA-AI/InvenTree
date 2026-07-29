"""Service-level tests for the part verification (Right-Part Finder) commands.

Covers feature-flag gating, session creation, deterministic evaluation, the
evidence lifecycle, candidate rejection/confirmation, no-safe-match,
invalidation, reevaluation, cancellation, consumer use binding, and the
permission matrix.
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.utils import timezone

from assets.models import AssetMachine, Client, MachinePart
from common.models import Parameter, ParameterTemplate
from part.models import Part, PartRelated
from part.verification import services
from part.verification.errors import (
    VerificationCandidateIneligible,
    VerificationCandidateStale,
    VerificationContextInvalid,
    VerificationDisabled,
    VerificationIdempotencyConflict,
    VerificationNoSafeMatchInvalid,
    VerificationNotFound,
    VerificationPermissionError,
    VerificationPolicyUnavailable,
    VerificationRevisionConflict,
    VerificationScopeError,
    VerificationSessionExpired,
    VerificationSessionStale,
    VerificationStateConflict,
    VerificationUseError,
)
from part.verification.policy import create_policy_version
from part.verification.schema import (
    BlockerCodes,
    CommandCodes,
    ConsumerCodes,
    DecisionKind,
    EventType,
    EvidenceDecision,
    PartVerificationState,
)
from part.verification.scope import VerificationScope
from part.verification_models import PartVerificationDecision, PartVerificationSession

FLAG_SETTINGS = {
    'AIMMS_RPF_ENABLED': True,
    'AIMMS_RPF_COLLECTION_ENABLED': True,
    'AIMMS_RPF_EVALUATION_ENABLED': True,
    'AIMMS_RPF_CONFIRMATION_ENABLED': True,
}

GLOBAL_SCOPE = VerificationScope(customer_id=None, site_key=None)


def _policy_definition(voltage_template: str, phase_template: str) -> dict:
    """Return a valid two-requirement policy document for the given templates."""
    return {
        'schema_version': 1,
        'description': 'Verification service test policy',
        'requirements': [
            {
                'key': 'electrical.voltage',
                'category': 'electrical',
                'value_kind': 'range',
                'operator': 'range_within',
                'unit': 'V',
                'hard': True,
                'sources': [
                    {'kind': 'observation'},
                    {'kind': 'parameter', 'template': voltage_template},
                ],
                'missing_blocker': 'NAMEPLATE_REQUIRED',
            },
            {
                'key': 'electrical.phase',
                'value_kind': 'decimal',
                'operator': 'eq',
                'unit': '',
                'hard': True,
                'sources': [
                    {'kind': 'observation'},
                    {'kind': 'parameter', 'template': phase_template},
                ],
                'candidate_missing': 'exclude',
                'conflict_code': 'PHASE_CONFLICT',
            },
        ],
        'retrieval': {'max_candidates': 50, 'tier_cap': 25},
        'rank_factors': [
            {'id': 'exact_requested_identity', 'max': 25},
            {'id': 'exact_application_relation', 'max': 25},
            {'id': 'evidence_coverage', 'max': 15},
            {'id': 'preferred_representation', 'max': 4},
            {'id': 'freshness', 'max': 3},
        ],
        'revalidation': {
            'non_material_paths': ['requested_part.description'],
            'expiry_hours': 24,
        },
    }


@override_settings(**FLAG_SETTINGS)
class VerificationServiceFixture(TestCase):
    """Shared fixture: active policy, catalog trio, and a superuser actor."""

    PREFIX = 'RPFSX'

    @classmethod
    def setUpTestData(cls):
        """Create the policy, parameter templates, parts, and actor."""
        prefix = cls.PREFIX
        cls.user = get_user_model().objects.create_superuser(
            username=f'{prefix}-admin', email=f'{prefix}@example.com', password='x'
        )

        cls.voltage_template = ParameterTemplate.objects.create(
            name=f'{prefix}Voltage', units='V'
        )
        cls.phase_template = ParameterTemplate.objects.create(name=f'{prefix}Phase')

        cls.policy = create_policy_version(
            key='rpf-core',
            version=1,
            definition=_policy_definition(f'{prefix}Voltage', f'{prefix}Phase'),
            activate=True,
        )

        cls.requested = Part.objects.create(
            name=f'{prefix} Motor A', IPN=f'{prefix}-001', active=True, component=True
        )
        cls.good = Part.objects.create(
            name=f'{prefix} Motor B', IPN=f'{prefix}-002', active=True, component=True
        )
        cls.bad = Part.objects.create(
            name=f'{prefix} Motor C', IPN=f'{prefix}-003', active=True, component=True
        )
        PartRelated.objects.create(part_1=cls.requested, part_2=cls.good)
        PartRelated.objects.create(part_1=cls.requested, part_2=cls.bad)

        # A part with no related candidates, used for no-safe-match flows
        cls.lonely = Part.objects.create(
            name=f'{prefix} Lonely', IPN=f'{prefix}-LON', active=True, component=True
        )

        for part, volts, phase in (
            (cls.requested, '460', '3'),
            (cls.good, '460', '3'),
            (cls.bad, '460', '1'),
            (cls.lonely, '460', '3'),
        ):
            cls._add_param(part, cls.voltage_template, volts)
            cls._add_param(part, cls.phase_template, phase)

    def setUp(self):
        """Grant the actor the explicit global verification scope."""
        super().setUp()
        self.user.verification_scopes = {GLOBAL_SCOPE}

    @classmethod
    def _add_param(cls, part, template, value):
        """Attach one generic parameter row to a part."""
        return Parameter.objects.create(
            model_type=ContentType.objects.get_for_model(Part),
            model_id=part.pk,
            template=template,
            data=value,
        )

    def _manual_session(self, tag, part=None):
        """Create one manual-purpose session for the given requested part."""
        target = part if part is not None else self.requested
        return services.create_session(
            purpose='manual',
            actor=self.user,
            idempotency_key=f'{tag}-create',
            requested_part_id=target.pk,
        )

    def _reviewed_session(self, tag, part=None):
        """Create and evaluate one manual session; return (session, result)."""
        session = self._manual_session(tag, part=part)
        result = services.evaluate_session(
            session_id=session.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key=f'{tag}-eval',
        )
        session.refresh_from_db()
        return session, result

    def _evaluation_for(self, session, part):
        """Return the current-revision candidate evaluation row for a part."""
        return session.candidate_evaluations.get(
            session_revision=session.revision, candidate=part
        )

    def _confirmed_decision(self, tag):
        """Create, evaluate, and confirm one session; return (session, decision)."""
        session, _ = self._reviewed_session(tag)
        evaluation = self._evaluation_for(session, self.good)
        decision = services.confirm_candidate(
            session_id=session.pk,
            evaluation_id=evaluation.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key=f'{tag}-confirm',
            reason='service test confirmation',
        )
        session.refresh_from_db()
        return session, decision

    def _no_safe_match_decision(self, tag):
        """Drive one lonely-part session to a no-safe-match decision."""
        session = self._manual_session(tag, part=self.lonely)
        evidence = services.attach_evidence(
            session_id=session.pk,
            actor=self.user,
            idempotency_key=f'{tag}-attach',
            requirement_key='electrical.phase',
            value='1',
        )
        services.decide_evidence(
            session_id=session.pk,
            evidence_id=evidence.pk,
            actor=self.user,
            idempotency_key=f'{tag}-accept',
            accept=True,
        )
        services.evaluate_session(
            session_id=session.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key=f'{tag}-eval',
        )
        decision = services.mark_no_safe_match(
            session_id=session.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key=f'{tag}-mark',
            reason='no safe candidate exists',
        )
        session.refresh_from_db()
        return session, decision


class VerificationFeatureFlagTests(TestCase):
    """Feature flags fail closed for every verification command."""

    def _all_commands(self):
        """Return one callable per verification command, using dummy context."""
        actor = None
        return {
            'create_session': lambda: services.create_session(
                purpose='manual', actor=actor, idempotency_key='flag-create'
            ),
            'attach_evidence': lambda: services.attach_evidence(
                session_id=1,
                actor=actor,
                idempotency_key='flag-attach',
                requirement_key='electrical.voltage',
                value='460',
            ),
            'decide_evidence': lambda: services.decide_evidence(
                session_id=1,
                evidence_id=1,
                actor=actor,
                idempotency_key='flag-decide',
                accept=True,
            ),
            'evaluate_session': lambda: services.evaluate_session(
                session_id=1,
                actor=actor,
                expected_revision=1,
                idempotency_key='flag-eval',
            ),
            'reevaluate_session': lambda: services.reevaluate_session(
                session_id=1,
                actor=actor,
                expected_revision=1,
                idempotency_key='flag-reeval',
            ),
            'reject_candidate': lambda: services.reject_candidate(
                session_id=1,
                evaluation_id=1,
                actor=actor,
                expected_revision=1,
                idempotency_key='flag-reject',
                reason='r',
            ),
            'confirm_candidate': lambda: services.confirm_candidate(
                session_id=1,
                evaluation_id=1,
                actor=actor,
                expected_revision=1,
                idempotency_key='flag-confirm',
                reason='r',
            ),
            'mark_no_safe_match': lambda: services.mark_no_safe_match(
                session_id=1,
                actor=actor,
                expected_revision=1,
                idempotency_key='flag-nsm',
                reason='r',
            ),
            'invalidate_session': lambda: services.invalidate_session(
                session_id=1, actor=actor, idempotency_key='flag-inv', reason='r'
            ),
            'cancel_session': lambda: services.cancel_session(
                session_id=1, actor=actor, idempotency_key='flag-cancel'
            ),
            'validate_and_bind_use': lambda: services.validate_and_bind_use(
                decision_id=1,
                actor=actor,
                consumer_kind='job_kit',
                consumer_action='substitution_decide',
                idempotency_key='flag-use',
            ),
        }

    @override_settings(AIMMS_RPF_ENABLED=False)
    def test_master_flag_off_disables_every_command(self):
        """Test that every command raises VerificationDisabled when RPF is off."""
        for name, command in self._all_commands().items():
            with self.subTest(command=name), self.assertRaises(VerificationDisabled):
                command()

    @override_settings(
        AIMMS_RPF_ENABLED=True,
        AIMMS_RPF_COLLECTION_ENABLED=False,
        AIMMS_RPF_EVALUATION_ENABLED=True,
        AIMMS_RPF_CONFIRMATION_ENABLED=True,
    )
    def test_collection_flag_off_disables_create(self):
        """Test that create_session is disabled when collection is off."""
        with self.assertRaises(VerificationDisabled):
            services.create_session(
                purpose='manual', actor=None, idempotency_key='subflag-create'
            )

    @override_settings(
        AIMMS_RPF_ENABLED=True,
        AIMMS_RPF_COLLECTION_ENABLED=True,
        AIMMS_RPF_EVALUATION_ENABLED=False,
        AIMMS_RPF_CONFIRMATION_ENABLED=True,
    )
    def test_evaluation_flag_off_disables_evaluate(self):
        """Test that evaluate_session is disabled when evaluation is off."""
        with self.assertRaises(VerificationDisabled):
            services.evaluate_session(
                session_id=1,
                actor=None,
                expected_revision=1,
                idempotency_key='subflag-eval',
            )

    @override_settings(
        AIMMS_RPF_ENABLED=True,
        AIMMS_RPF_COLLECTION_ENABLED=True,
        AIMMS_RPF_EVALUATION_ENABLED=True,
        AIMMS_RPF_CONFIRMATION_ENABLED=False,
    )
    def test_confirmation_flag_off_disables_confirm(self):
        """Test that confirm_candidate is disabled when confirmation is off."""
        with self.assertRaises(VerificationDisabled):
            services.confirm_candidate(
                session_id=1,
                evaluation_id=1,
                actor=None,
                expected_revision=1,
                idempotency_key='subflag-confirm',
                reason='r',
            )


class CreateSessionServiceTests(VerificationServiceFixture):
    """Behavior of the create_session command."""

    PREFIX = 'RPFSA'

    @classmethod
    def setUpTestData(cls):
        """Add client tenants and machines to the shared fixture."""
        super().setUpTestData()
        suffix_a = uuid.uuid4().hex[:10]
        suffix_b = uuid.uuid4().hex[:10]
        cls.client_a = Client.objects.create(
            name=f'Tenant {suffix_a}', code=f'tenant-{suffix_a}'
        )
        cls.client_b = Client.objects.create(
            name=f'Tenant {suffix_b}', code=f'tenant-{suffix_b}'
        )
        cls.machine = AssetMachine.objects.create(name=f'{cls.PREFIX} Machine')
        cls.machine_part = MachinePart.objects.create(
            machine=cls.machine, part=cls.requested, quantity=1
        )
        cls.client_machine = AssetMachine.objects.create(
            name=f'{cls.PREFIX} Client Machine', client=cls.client_b
        )

    def test_create_happy_path(self):
        """Test that a new session starts collecting with a reference and event."""
        session = self._manual_session('create-happy')

        self.assertEqual(session.state, PartVerificationState.COLLECTING)
        self.assertTrue(session.reference.startswith('PVS-'))
        self.assertEqual(session.revision, 1)
        self.assertEqual(session.policy_id, self.policy.pk)
        self.assertIsNone(session.scope_customer_id)
        self.assertIsNone(session.scope_client_id)
        self.assertTrue(
            session.events.filter(event_type=EventType.SESSION_CREATED).exists()
        )

    def test_create_exact_replay_returns_same_session(self):
        """Test that an exact same-key replay returns the same session row."""
        session = self._manual_session('create-replay')
        replay = self._manual_session('create-replay')

        self.assertEqual(session.pk, replay.pk)
        self.assertEqual(PartVerificationSession.objects.count(), 1)

    def test_create_same_key_different_payload_conflicts(self):
        """Test that reusing a key with a different payload is a stable conflict."""
        self._manual_session('create-conflict')

        with self.assertRaises(VerificationIdempotencyConflict):
            services.create_session(
                purpose='manual',
                actor=self.user,
                idempotency_key='create-conflict-create',
                requested_part_id=self.good.pk,
            )

    def test_create_unknown_context_not_found(self):
        """Test that an unknown context object id raises a scope-safe not found."""
        with self.assertRaises(VerificationNotFound):
            services.create_session(
                purpose='installed_replacement',
                actor=self.user,
                idempotency_key='create-unknown',
                machine_id=999999,
            )

    def test_create_machine_part_implies_machine_and_requested_part(self):
        """Test that binding an installed row derives machine and requested part."""
        session = services.create_session(
            purpose='installed_replacement',
            actor=self.user,
            idempotency_key='create-implied',
            machine_part_id=self.machine_part.pk,
        )

        self.assertEqual(session.machine_id, self.machine.pk)
        self.assertEqual(session.requested_part_id, self.requested.pk)
        self.assertEqual(session.machine_part_id, self.machine_part.pk)

    def test_create_scope_mismatch_is_scope_safe(self):
        """Test that an out-of-scope context is indistinguishable from absent.

        A scope mismatch during creation returns the same not-found error as a
        nonexistent context id (spec 17.3 rule 7), so session creation cannot
        be used as an existence oracle for another tenant's machines.
        """
        self.user.verification_scopes = {
            VerificationScope(
                customer_id=None, site_key=None, client_id=self.client_a.pk
            )
        }

        with self.assertRaises(VerificationNotFound):
            services.create_session(
                purpose='manual',
                actor=self.user,
                idempotency_key='create-scope',
                requested_part_id=self.requested.pk,
                machine_id=self.client_machine.pk,
            )

        self.assertEqual(PartVerificationSession.objects.count(), 0)

    def test_create_on_client_machine_records_client_scope(self):
        """Test that a client-owned machine context stamps the client scope."""
        self.user.verification_scopes = {
            VerificationScope(
                customer_id=None, site_key=None, client_id=self.client_b.pk
            )
        }

        session = services.create_session(
            purpose='installed_replacement',
            actor=self.user,
            idempotency_key='create-client-scope',
            requested_part_id=self.requested.pk,
            machine_id=self.client_machine.pk,
        )

        self.assertIsNone(session.scope_customer_id)
        self.assertEqual(session.scope_client_id, self.client_b.pk)

    def test_create_unresolved_scope_still_reports_scope_error(self):
        """Test that an unresolved actor scope keeps its own stable code.

        Unresolved scope reveals nothing about any target object, so it stays
        a scope error rather than a scope-safe 404.
        """
        self.user.verification_scopes = set()

        with self.assertRaises(VerificationScopeError) as ctx:
            services.create_session(
                purpose='manual',
                actor=self.user,
                idempotency_key='create-scope-unresolved',
                requested_part_id=self.requested.pk,
            )

        self.assertEqual(ctx.exception.code, CommandCodes.RPF_SCOPE_UNRESOLVED)
        self.assertEqual(PartVerificationSession.objects.count(), 0)

    def test_create_without_active_policy(self):
        """Test that a missing active policy fails closed."""
        with override_settings(AIMMS_RPF_POLICY_KEY='rpf-missing'):
            with self.assertRaises(VerificationPolicyUnavailable):
                self._manual_session('create-nopolicy')


class EvaluateSessionServiceTests(VerificationServiceFixture):
    """Behavior of the evaluate_session command."""

    PREFIX = 'RPFSB'

    def test_evaluate_wrong_revision_conflicts(self):
        """Test that a wrong expected revision raises a revision conflict."""
        session = self._manual_session('eval-rev')

        with self.assertRaises(VerificationRevisionConflict):
            services.evaluate_session(
                session_id=session.pk,
                actor=self.user,
                expected_revision=5,
                idempotency_key='eval-rev-eval',
            )

    def test_evaluate_from_confirmed_state_conflicts(self):
        """Test that a confirmed session cannot be evaluated again."""
        session, _ = self._confirmed_decision('eval-confirmed')

        with self.assertRaises(VerificationStateConflict):
            services.evaluate_session(
                session_id=session.pk,
                actor=self.user,
                expected_revision=1,
                idempotency_key='eval-confirmed-again',
            )

    def test_evaluate_blocked_returns_blockers_and_stays_collecting(self):
        """Test that a missing hard fact blocks evaluation with stable codes."""
        novolt = Part.objects.create(
            name=f'{self.PREFIX} NoVolt',
            IPN=f'{self.PREFIX}-NOV',
            active=True,
            component=True,
        )
        self._add_param(novolt, self.phase_template, '3')

        session = self._manual_session('eval-blocked', part=novolt)
        result = services.evaluate_session(
            session_id=session.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key='eval-blocked-eval',
        )
        session.refresh_from_db()

        self.assertEqual(result['state'], PartVerificationState.COLLECTING)
        self.assertEqual(session.state, PartVerificationState.COLLECTING)
        self.assertTrue(result['blockers'])
        codes = {blocker['code'] for blocker in result['blockers']}
        self.assertIn(BlockerCodes.NAMEPLATE_REQUIRED, codes)
        self.assertTrue(
            session.events.filter(event_type=EventType.EVALUATION_BLOCKED).exists()
        )

    def test_evaluate_happy_path(self):
        """Test that evaluation ranks survivors and sets review metadata."""
        session, result = self._reviewed_session('eval-happy')

        self.assertEqual(result['state'], PartVerificationState.REVIEW_REQUIRED)
        self.assertEqual(result['blockers'], [])
        self.assertTrue(result['universe_complete'])
        self.assertEqual(result['considered'], 3)
        self.assertEqual(result['eligible'], 2)

        self.assertEqual(session.state, PartVerificationState.REVIEW_REQUIRED)
        self.assertIsNotNone(session.expires_at)
        self.assertTrue(session.universe_complete)
        self.assertEqual(session.considered_count, 3)
        self.assertEqual(session.eligible_count, 2)

        requested_eval = self._evaluation_for(session, self.requested)
        bad_eval = self._evaluation_for(session, self.bad)
        self.assertTrue(requested_eval.eligible)
        self.assertEqual(requested_eval.rank, 1)
        self.assertFalse(bad_eval.eligible)
        self.assertIsNone(bad_eval.rank)

        self.assertTrue(
            session.events.filter(event_type=EventType.SESSION_EVALUATED).exists()
        )


class EvidenceLifecycleServiceTests(VerificationServiceFixture):
    """Attach/decide evidence lifecycle and its consumption during evaluation."""

    PREFIX = 'RPFSC'

    def test_accepted_evidence_consumed_as_observation_fact(self):
        """Test that accepted evidence wins requirement resolution as observation."""
        session = self._manual_session('ev-acc')

        evidence = services.attach_evidence(
            session_id=session.pk,
            actor=self.user,
            idempotency_key='ev-acc-attach',
            requirement_key='electrical.voltage',
            value='460',
            unit='V',
        )
        self.assertEqual(evidence.decision, EvidenceDecision.PROPOSED)

        evidence = services.decide_evidence(
            session_id=session.pk,
            evidence_id=evidence.pk,
            actor=self.user,
            idempotency_key='ev-acc-accept',
            accept=True,
        )
        self.assertEqual(evidence.decision, EvidenceDecision.ACCEPTED)

        services.evaluate_session(
            session_id=session.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key='ev-acc-eval',
        )

        requirement = session.requirements.get(key='electrical.voltage')
        self.assertEqual(requirement.authority, 'observation')
        self.assertTrue(
            any(
                entry.get('evidence_id') == evidence.pk
                for entry in requirement.provenance
            )
        )

    def test_rejected_evidence_not_consumed(self):
        """Test that rejected evidence never resolves a requirement."""
        session = self._manual_session('ev-rej')

        evidence = services.attach_evidence(
            session_id=session.pk,
            actor=self.user,
            idempotency_key='ev-rej-attach',
            requirement_key='electrical.voltage',
            value='500',
            unit='V',
        )
        evidence = services.decide_evidence(
            session_id=session.pk,
            evidence_id=evidence.pk,
            actor=self.user,
            idempotency_key='ev-rej-reject',
            accept=False,
        )
        self.assertEqual(evidence.decision, EvidenceDecision.REJECTED)

        result = services.evaluate_session(
            session_id=session.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key='ev-rej-eval',
        )

        # The requirement falls back to the catalog parameter source
        requirement = session.requirements.get(key='electrical.voltage')
        self.assertTrue(requirement.authority.startswith('parameter:'))
        self.assertFalse(
            any('evidence_id' in entry for entry in requirement.provenance)
        )
        # Parameter voltage (460) keeps the compatible candidates eligible
        self.assertEqual(result['eligible'], 2)

    def test_redeciding_decided_evidence_conflicts(self):
        """Test that decided evidence cannot be re-decided."""
        session = self._manual_session('ev-redecide')

        evidence = services.attach_evidence(
            session_id=session.pk,
            actor=self.user,
            idempotency_key='ev-redecide-attach',
            requirement_key='electrical.voltage',
            value='460',
            unit='V',
        )
        services.decide_evidence(
            session_id=session.pk,
            evidence_id=evidence.pk,
            actor=self.user,
            idempotency_key='ev-redecide-accept',
            accept=True,
        )

        with self.assertRaises(VerificationStateConflict):
            services.decide_evidence(
                session_id=session.pk,
                evidence_id=evidence.pk,
                actor=self.user,
                idempotency_key='ev-redecide-again',
                accept=False,
            )

    def test_attach_evidence_with_nested_float_value(self):
        """Test that nested binary floats in client JSON never crash hashing.

        JSON numeric literals arrive as floats; canonical hashing prohibits
        floats, so attach must deep-coerce them (to strings) instead of
        raising an unhandled CanonicalizationError.
        """
        session = self._manual_session('ev-float')

        evidence = services.attach_evidence(
            session_id=session.pk,
            actor=self.user,
            idempotency_key='ev-float-attach',
            requirement_key='electrical.voltage',
            value={'min': 440.5, 'max': 480.5},
            unit='V',
        )
        self.assertEqual(evidence.raw_value, {'min': '440.5', 'max': '480.5'})

        services.decide_evidence(
            session_id=session.pk,
            evidence_id=evidence.pk,
            actor=self.user,
            idempotency_key='ev-float-accept',
            accept=True,
        )
        result = services.evaluate_session(
            session_id=session.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key='ev-float-eval',
        )
        self.assertEqual(result['state'], PartVerificationState.REVIEW_REQUIRED)


class CandidateDecisionServiceTests(VerificationServiceFixture):
    """Candidate rejection and confirmation, including staleness rechecks."""

    PREFIX = 'RPFSD'

    @classmethod
    def setUpTestData(cls):
        """Add an asset machine context so source drift can be exercised."""
        super().setUpTestData()
        cls.machine = AssetMachine.objects.create(
            name=f'{cls.PREFIX} Machine', model='MOD-1'
        )
        MachinePart.objects.create(machine=cls.machine, part=cls.requested, quantity=1)

    def _reviewed_machine_session(self, tag):
        """Create and evaluate one installed-replacement session."""
        session = services.create_session(
            purpose='installed_replacement',
            actor=self.user,
            idempotency_key=f'{tag}-create',
            requested_part_id=self.requested.pk,
            machine_id=self.machine.pk,
        )
        services.evaluate_session(
            session_id=session.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key=f'{tag}-eval',
        )
        session.refresh_from_db()
        self.assertEqual(session.state, PartVerificationState.REVIEW_REQUIRED)
        return session

    def _confirm(self, session, evaluation, key, reason='service test confirmation'):
        """Confirm one candidate evaluation on a session."""
        return services.confirm_candidate(
            session_id=session.pk,
            evaluation_id=evaluation.pk,
            actor=self.user,
            expected_revision=session.revision,
            idempotency_key=key,
            reason=reason,
        )

    def test_reject_requires_reason(self):
        """Test that a rejection without a reason is invalid."""
        session = self._reviewed_machine_session('rej-reason')
        evaluation = self._evaluation_for(session, self.good)

        with self.assertRaises(VerificationContextInvalid):
            services.reject_candidate(
                session_id=session.pk,
                evaluation_id=evaluation.pk,
                actor=self.user,
                expected_revision=1,
                idempotency_key='rej-reason-reject',
                reason='',
            )

    def test_rejected_candidate_cannot_be_confirmed(self):
        """Test that rejection is recorded and blocks later confirmation."""
        session = self._reviewed_machine_session('rej-block')
        evaluation = self._evaluation_for(session, self.good)

        rejected = services.reject_candidate(
            session_id=session.pk,
            evaluation_id=evaluation.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key='rej-block-reject',
            reason='wrong brand for this site',
        )
        self.assertTrue(rejected.rejected)
        self.assertEqual(rejected.rejected_reason, 'wrong brand for this site')
        self.assertTrue(rejected.eligible)
        self.assertTrue(
            session.events.filter(event_type=EventType.CANDIDATE_REJECTED).exists()
        )

        with self.assertRaises(VerificationCandidateIneligible):
            self._confirm(session, rejected, 'rej-block-confirm')

    def test_confirm_happy_path(self):
        """Test that confirmation records the decision and pointer atomically."""
        session = self._reviewed_machine_session('conf-happy')
        evaluation = self._evaluation_for(session, self.good)

        decision = self._confirm(session, evaluation, 'conf-happy-confirm')
        session.refresh_from_db()

        self.assertEqual(decision.kind, DecisionKind.CONFIRMED)
        self.assertEqual(decision.selected_part_id, self.good.pk)
        self.assertEqual(decision.selected_evaluation_id, evaluation.pk)
        self.assertIsNotNone(decision.valid_until)
        self.assertEqual(session.state, PartVerificationState.CONFIRMED)
        self.assertEqual(session.current_decision_id, decision.pk)
        self.assertTrue(
            session.events.filter(event_type=EventType.SESSION_CONFIRMED).exists()
        )

    def test_confirm_excluded_candidate_ineligible(self):
        """Test that an excluded candidate can never be confirmed."""
        session = self._reviewed_machine_session('conf-excluded')
        evaluation = self._evaluation_for(session, self.bad)
        self.assertFalse(evaluation.eligible)

        with self.assertRaises(VerificationCandidateIneligible):
            self._confirm(session, evaluation, 'conf-excluded-confirm')

    def test_confirm_expired_session(self):
        """Test that an expired evaluation window blocks confirmation."""
        session = self._reviewed_machine_session('conf-expired')
        evaluation = self._evaluation_for(session, self.good)

        session.expires_at = timezone.now() - timedelta(hours=1)
        session.save(update_fields=['expires_at'])

        with self.assertRaises(VerificationSessionExpired):
            self._confirm(session, evaluation, 'conf-expired-confirm')

    def test_confirm_source_drift_marks_stale(self):
        """Test that source drift is detected under lock and persisted as stale."""
        session = self._reviewed_machine_session('conf-drift')
        evaluation = self._evaluation_for(session, self.good)

        self.machine.model = 'MOD-2-DRIFTED'
        self.machine.save()

        with self.assertRaises(VerificationSessionStale):
            self._confirm(session, evaluation, 'conf-drift-confirm')

        session.refresh_from_db()
        self.assertEqual(session.state, PartVerificationState.STALE)
        self.assertEqual(session.stale_reason, 'source_changed')
        self.assertTrue(
            session.events.filter(event_type=EventType.SESSION_STALE).exists()
        )

    def test_confirm_candidate_drift_marks_stale(self):
        """Test that candidate fact drift is detected and persisted as stale."""
        session = self._reviewed_machine_session('conf-cdrift')
        evaluation = self._evaluation_for(session, self.good)

        param = Parameter.objects.get(
            model_type=ContentType.objects.get_for_model(Part),
            model_id=self.good.pk,
            template=self.phase_template,
        )
        param.data = '1'
        param.save()

        with self.assertRaises(VerificationCandidateStale):
            self._confirm(session, evaluation, 'conf-cdrift-confirm')

        session.refresh_from_db()
        self.assertEqual(session.state, PartVerificationState.STALE)
        self.assertEqual(session.stale_reason, 'candidate_changed')

    def test_confirm_exact_replay_returns_same_decision(self):
        """Test that an exact same-key confirm replay returns the same decision."""
        session = self._reviewed_machine_session('conf-replay')
        evaluation = self._evaluation_for(session, self.good)

        decision = self._confirm(session, evaluation, 'conf-replay-confirm')
        session.refresh_from_db()
        replay = self._confirm(session, evaluation, 'conf-replay-confirm')

        self.assertEqual(decision.pk, replay.pk)
        self.assertEqual(session.decisions.count(), 1)

    def test_second_confirm_with_new_key_conflicts(self):
        """Test that a confirmed session refuses a second, differently-keyed confirm."""
        session = self._reviewed_machine_session('conf-second')
        evaluation = self._evaluation_for(session, self.good)
        self._confirm(session, evaluation, 'conf-second-confirm')
        session.refresh_from_db()

        with self.assertRaises(VerificationStateConflict):
            self._confirm(session, evaluation, 'conf-second-confirm-2')

        self.assertEqual(session.decisions.count(), 1)


class NoSafeMatchServiceTests(VerificationServiceFixture):
    """Behavior of the mark_no_safe_match command."""

    PREFIX = 'RPFSE'

    def test_no_safe_match_rejected_while_survivors_exist(self):
        """Test that no-safe-match is not a shortcut past eligible candidates."""
        session, result = self._reviewed_session('nsm-survivors')
        self.assertEqual(result['eligible'], 2)

        with self.assertRaises(VerificationNoSafeMatchInvalid):
            services.mark_no_safe_match(
                session_id=session.pk,
                actor=self.user,
                expected_revision=1,
                idempotency_key='nsm-survivors-mark',
                reason='should be refused',
            )

    def test_no_safe_match_happy_path_all_excluded(self):
        """Test that a complete all-excluded universe records a no-match decision."""
        session = self._manual_session('nsm-happy', part=self.lonely)

        evidence = services.attach_evidence(
            session_id=session.pk,
            actor=self.user,
            idempotency_key='nsm-happy-attach',
            requirement_key='electrical.phase',
            value='1',
        )
        services.decide_evidence(
            session_id=session.pk,
            evidence_id=evidence.pk,
            actor=self.user,
            idempotency_key='nsm-happy-accept',
            accept=True,
        )
        result = services.evaluate_session(
            session_id=session.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key='nsm-happy-eval',
        )
        self.assertTrue(result['universe_complete'])
        self.assertEqual(result['considered'], 1)
        self.assertEqual(result['eligible'], 0)

        decision = services.mark_no_safe_match(
            session_id=session.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key='nsm-happy-mark',
            reason='observed phase excludes every candidate',
        )
        session.refresh_from_db()

        self.assertEqual(decision.kind, DecisionKind.NO_SAFE_MATCH)
        self.assertIsNone(decision.selected_part_id)
        self.assertIsNone(decision.selected_evaluation_id)
        self.assertEqual(session.state, PartVerificationState.NO_SAFE_MATCH)
        self.assertEqual(session.current_decision_id, decision.pk)
        self.assertTrue(
            session.events.filter(event_type=EventType.NO_SAFE_MATCH_RECORDED).exists()
        )


class SessionLifecycleServiceTests(VerificationServiceFixture):
    """Invalidation, reevaluation, and cancellation lifecycle."""

    PREFIX = 'RPFSF'

    def test_invalidate_from_confirmed(self):
        """Test that invalidation turns a confirmed session stale."""
        session, _ = self._confirmed_decision('inv-confirmed')

        result = services.invalidate_session(
            session_id=session.pk,
            actor=self.user,
            idempotency_key='inv-confirmed-inv',
            reason='field report contradicts nameplate',
        )
        session.refresh_from_db()

        self.assertEqual(result.state, PartVerificationState.STALE)
        self.assertEqual(session.state, PartVerificationState.STALE)
        self.assertEqual(session.stale_reason, 'invalidated')
        self.assertTrue(
            session.events.filter(event_type=EventType.SESSION_STALE).exists()
        )

    def test_invalidate_from_collecting_conflicts(self):
        """Test that an undecided session cannot be invalidated."""
        session = self._manual_session('inv-collecting')

        with self.assertRaises(VerificationStateConflict):
            services.invalidate_session(
                session_id=session.pk,
                actor=self.user,
                idempotency_key='inv-collecting-inv',
                reason='not decided yet',
            )

    def test_reevaluate_requires_stale_state(self):
        """Test that reevaluation is only permitted from the stale state."""
        session, _ = self._reviewed_session('reeval-state')

        with self.assertRaises(VerificationStateConflict):
            services.reevaluate_session(
                session_id=session.pk,
                actor=self.user,
                expected_revision=1,
                idempotency_key='reeval-state-re',
            )

    def test_reevaluate_increments_revision_and_preserves_history(self):
        """Test that reevaluation opens a new revision without rewriting history."""
        session, decision = self._confirmed_decision('reeval-hist')
        original_hash = decision.decision_hash

        services.invalidate_session(
            session_id=session.pk,
            actor=self.user,
            idempotency_key='reeval-hist-inv',
            reason='drift suspected',
        )

        result = services.reevaluate_session(
            session_id=session.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key='reeval-hist-re',
        )
        session.refresh_from_db()

        self.assertEqual(result['revision'], 2)
        self.assertEqual(result['state'], PartVerificationState.REVIEW_REQUIRED)
        self.assertEqual(session.revision, 2)
        self.assertIsNone(session.current_decision_id)

        # Prior-revision evaluations are retained beside the new revision
        self.assertEqual(
            session.candidate_evaluations.filter(session_revision=1).count(), 3
        )
        self.assertEqual(
            session.candidate_evaluations.filter(session_revision=2).count(), 3
        )

        # The superseded decision row remains immutable
        decision.refresh_from_db()
        self.assertEqual(decision.kind, DecisionKind.CONFIRMED)
        self.assertEqual(decision.selected_part_id, self.good.pk)
        self.assertEqual(decision.decision_hash, original_hash)
        self.assertEqual(decision.session_revision, 1)

    def test_cancel_from_collecting_and_idempotent(self):
        """Test that cancellation works from collecting and replays as a no-op."""
        session = self._manual_session('cancel-collecting')

        services.cancel_session(
            session_id=session.pk,
            actor=self.user,
            idempotency_key='cancel-collecting-1',
            reason='operator abandoned the flow',
        )
        session.refresh_from_db()
        self.assertEqual(session.state, PartVerificationState.CANCELLED)

        # A repeated cancel (any key) is an idempotent no-op
        repeat = services.cancel_session(
            session_id=session.pk,
            actor=self.user,
            idempotency_key='cancel-collecting-2',
        )
        self.assertEqual(repeat.state, PartVerificationState.CANCELLED)
        self.assertEqual(
            session.events.filter(event_type=EventType.SESSION_CANCELLED).count(), 1
        )

    def test_cancel_from_review(self):
        """Test that cancellation is permitted from review."""
        session, _ = self._reviewed_session('cancel-review')

        services.cancel_session(
            session_id=session.pk, actor=self.user, idempotency_key='cancel-review-1'
        )
        session.refresh_from_db()
        self.assertEqual(session.state, PartVerificationState.CANCELLED)

    def test_cancel_from_confirmed_conflicts(self):
        """Test that a confirmed session cannot be cancelled."""
        session, _ = self._confirmed_decision('cancel-confirmed')

        with self.assertRaises(VerificationStateConflict):
            services.cancel_session(
                session_id=session.pk,
                actor=self.user,
                idempotency_key='cancel-confirmed-1',
            )


class ValidateAndBindUseServiceTests(VerificationServiceFixture):
    """The common consumer contract of validate_and_bind_use."""

    PREFIX = 'RPFSG'

    def _use(self, decision, key, **overrides):
        """Bind one consumer use for a decision with default expectations."""
        arguments = {
            'decision_id': decision.pk,
            'actor': self.user,
            'consumer_kind': 'job_kit',
            'consumer_action': 'substitution_decide',
            'idempotency_key': key,
            'expected_requested_part_id': self.requested.pk,
            'expected_selected_part_id': self.good.pk,
            'command_hash': 'sha256:test',
        }
        arguments.update(overrides)
        return services.validate_and_bind_use(**arguments)

    def test_wrong_requested_part_mismatch(self):
        """Test that a requested-part mismatch is refused with its stable code."""
        _, decision = self._confirmed_decision('use-req')

        with self.assertRaises(VerificationUseError) as ctx:
            self._use(decision, 'use-req-1', expected_requested_part_id=self.bad.pk)

        self.assertEqual(
            ctx.exception.code, ConsumerCodes.PART_VERIFICATION_REQUESTED_PART_MISMATCH
        )

    def test_wrong_selected_part_mismatch(self):
        """Test that a selected-part mismatch is refused with its stable code."""
        _, decision = self._confirmed_decision('use-sel')

        with self.assertRaises(VerificationUseError) as ctx:
            self._use(decision, 'use-sel-1', expected_selected_part_id=self.bad.pk)

        self.assertEqual(
            ctx.exception.code, ConsumerCodes.PART_VERIFICATION_SELECTED_PART_MISMATCH
        )

    def test_expired_decision_blocks_use(self):
        """Test that an expired decision validity window blocks use."""
        _, decision = self._confirmed_decision('use-expired')
        PartVerificationDecision.objects.filter(pk=decision.pk).update(
            valid_until=timezone.now() - timedelta(hours=1)
        )

        with self.assertRaises(VerificationUseError) as ctx:
            self._use(decision, 'use-expired-1')

        self.assertEqual(ctx.exception.code, ConsumerCodes.PART_VERIFICATION_EXPIRED)

    def test_stale_session_blocks_use(self):
        """Test that a stale session blocks use with its stable code."""
        session, decision = self._confirmed_decision('use-stale')
        services.invalidate_session(
            session_id=session.pk,
            actor=self.user,
            idempotency_key='use-stale-inv',
            reason='invalidated before use',
        )

        with self.assertRaises(VerificationUseError) as ctx:
            self._use(decision, 'use-stale-1')

        self.assertEqual(ctx.exception.code, ConsumerCodes.PART_VERIFICATION_STALE)

    def test_no_safe_match_decision_blocks_use(self):
        """Test that a no-safe-match decision blocks selecting effects."""
        _, decision = self._no_safe_match_decision('use-nsm')

        with self.assertRaises(VerificationUseError) as ctx:
            self._use(
                decision,
                'use-nsm-1',
                expected_requested_part_id=None,
                expected_selected_part_id=None,
            )

        self.assertEqual(
            ctx.exception.code, ConsumerCodes.PART_VERIFICATION_NO_SAFE_MATCH
        )

    def test_expected_scope_mismatch_blocks_use(self):
        """Test that a consumer expecting another scope is refused."""
        _, decision = self._confirmed_decision('use-scope')

        with self.assertRaises(VerificationUseError) as ctx:
            self._use(
                decision,
                'use-scope-1',
                expected_scope=VerificationScope(customer_id=999999, site_key=None),
            )

        self.assertEqual(
            ctx.exception.code, ConsumerCodes.PART_VERIFICATION_SCOPE_MISMATCH
        )

    def test_exact_replay_returns_same_use(self):
        """Test that a same-key same-command replay returns the same use row."""
        _, decision = self._confirmed_decision('use-replay')

        use = self._use(decision, 'use-replay-1')
        replay = self._use(decision, 'use-replay-1')

        self.assertEqual(use.pk, replay.pk)
        self.assertEqual(decision.uses.count(), 1)

    def test_same_key_different_command_hash_conflicts(self):
        """Test that a reused effect key with a different command is refused."""
        _, decision = self._confirmed_decision('use-conflict')
        self._use(decision, 'use-conflict-1')

        with self.assertRaises(VerificationUseError) as ctx:
            self._use(decision, 'use-conflict-1', command_hash='sha256:other')

        self.assertEqual(
            ctx.exception.code, ConsumerCodes.PART_VERIFICATION_USE_CONFLICT
        )

    def test_non_current_decision_not_confirmed(self):
        """Test that a superseded decision can no longer authorize use."""
        session, old_decision = self._confirmed_decision('use-super')

        services.invalidate_session(
            session_id=session.pk,
            actor=self.user,
            idempotency_key='use-super-inv',
            reason='reopen for reevaluation',
        )
        services.reevaluate_session(
            session_id=session.pk,
            actor=self.user,
            expected_revision=1,
            idempotency_key='use-super-re',
        )
        session.refresh_from_db()
        evaluation = self._evaluation_for(session, self.good)
        new_decision = services.confirm_candidate(
            session_id=session.pk,
            evaluation_id=evaluation.pk,
            actor=self.user,
            expected_revision=2,
            idempotency_key='use-super-confirm2',
            reason='reconfirmed after reevaluation',
        )
        self.assertNotEqual(new_decision.pk, old_decision.pk)

        with self.assertRaises(VerificationUseError) as ctx:
            self._use(old_decision, 'use-super-1')

        self.assertEqual(
            ctx.exception.code, ConsumerCodes.PART_VERIFICATION_NOT_CONFIRMED
        )

        # The current decision remains usable
        use = self._use(new_decision, 'use-super-2')
        self.assertEqual(use.decision_id, new_decision.pk)


class VerificationPermissionMatrixTests(VerificationServiceFixture):
    """Permission matrix using real Permission rows on non-superusers."""

    PREFIX = 'RPFSH'

    @classmethod
    def setUpTestData(cls):
        """Create non-superusers with distinct permission grants."""
        super().setUpTestData()
        user_model = get_user_model()

        cls.reviewer = user_model.objects.create_user(
            username=f'{cls.PREFIX}-reviewer', password='x'
        )
        reviewer_perms = Permission.objects.filter(
            content_type__app_label='part',
            codename__in=[
                'add_partverificationsession',
                'change_partverificationsession',
                'review_partverification',
            ],
        )
        assert reviewer_perms.count() == 3
        cls.reviewer.user_permissions.set(reviewer_perms)

        cls.confirmer = user_model.objects.create_user(
            username=f'{cls.PREFIX}-confirmer', password='x'
        )
        confirmer_perms = Permission.objects.filter(
            content_type__app_label='part',
            codename__in=[
                'change_partverificationsession',
                'review_partverification',
                'confirm_partverification',
            ],
        )
        assert confirmer_perms.count() == 3
        cls.confirmer.user_permissions.set(confirmer_perms)

    def setUp(self):
        """Re-fetch users to clear permission caches and grant scopes."""
        super().setUp()
        user_model = get_user_model()
        self.reviewer = user_model.objects.get(pk=self.reviewer.pk)
        self.reviewer.verification_scopes = {GLOBAL_SCOPE}
        self.confirmer = user_model.objects.get(pk=self.confirmer.pk)
        self.confirmer.verification_scopes = {GLOBAL_SCOPE}

    def test_confirm_requires_confirm_permission(self):
        """Test that change permission alone never authorizes confirmation."""
        self.assertFalse(self.reviewer.is_superuser)
        self.assertTrue(self.reviewer.has_perm('part.change_partverificationsession'))
        self.assertFalse(self.reviewer.has_perm('part.confirm_partverification'))

        session = services.create_session(
            purpose='manual',
            actor=self.reviewer,
            idempotency_key='perm-confirm-create',
            requested_part_id=self.requested.pk,
        )
        services.evaluate_session(
            session_id=session.pk,
            actor=self.reviewer,
            expected_revision=1,
            idempotency_key='perm-confirm-eval',
        )
        evaluation = self._evaluation_for(session, self.good)

        with self.assertRaises(VerificationPermissionError):
            services.confirm_candidate(
                session_id=session.pk,
                evaluation_id=evaluation.pk,
                actor=self.reviewer,
                expected_revision=1,
                idempotency_key='perm-confirm-confirm',
                reason='not authorized to confirm',
            )

        session.refresh_from_db()
        self.assertEqual(session.state, PartVerificationState.REVIEW_REQUIRED)
        self.assertEqual(session.decisions.count(), 0)

    def test_create_requires_add_permission(self):
        """Test that a user without add permission cannot create sessions."""
        self.assertFalse(self.confirmer.is_superuser)
        self.assertTrue(self.confirmer.has_perm('part.confirm_partverification'))
        self.assertFalse(self.confirmer.has_perm('part.add_partverificationsession'))

        with self.assertRaises(VerificationPermissionError):
            services.create_session(
                purpose='manual',
                actor=self.confirmer,
                idempotency_key='perm-create-create',
                requested_part_id=self.requested.pk,
            )

        self.assertEqual(PartVerificationSession.objects.count(), 0)
