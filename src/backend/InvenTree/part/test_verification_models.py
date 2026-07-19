"""Persistence-level tests for the Right-Part Finder verification models.

These tests exercise model registration, custom permission rows, uniqueness
constraints, check constraints, policy definition immutability, and PROTECT
delete behavior directly through the ORM (no service layer involved).
"""

import copy

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from part.models import Part
from part.verification.policy import activate_policy, revoke_policy
from part.verification_models import (
    PartCandidateEvaluation,
    PartVerificationCommand,
    PartVerificationDecision,
    PartVerificationPolicyVersion,
    PartVerificationRequirement,
    PartVerificationSession,
    PartVerificationUse,
)

MODEL_NAMES = [
    'PartVerificationPolicyVersion',
    'PartVerificationSession',
    'PartVerificationRequirement',
    'PartVerificationEvidence',
    'PartCandidateEvaluation',
    'PartVerificationDecision',
    'PartVerificationUse',
    'PartVerificationEvent',
    'PartVerificationCommand',
]

CUSTOM_PERMISSION_CODENAMES = [
    'review_partverification',
    'confirm_partverification',
    'invalidate_partverification',
    'use_partverification',
    'manage_partverificationpolicy',
]

# Minimal document accepted by part.verification.policy.validate_definition
VALID_DEFINITION = {
    'schema_version': 1,
    'description': 'model test policy',
    'requirements': [
        {
            'key': 'electrical.phase',
            'value_kind': 'decimal',
            'operator': 'eq',
            'hard': True,
            'sources': [{'kind': 'observation'}],
        }
    ],
}


def _hash(seed: int) -> str:
    """Return a syntactically valid, deterministic sha256 hash string."""
    return f'sha256:{seed:064x}'


def _make_policy(key: str = 'rpf-models', version: int = 1):
    """Create a draft policy version with a valid definition document."""
    return PartVerificationPolicyVersion.objects.create(
        key=key, version=version, definition=copy.deepcopy(VALID_DEFINITION)
    )


def _make_session(policy, seed: int = 1):
    """Create a minimal verification session bound to the given policy."""
    return PartVerificationSession.objects.create(
        purpose='manual', policy=policy, scope_fingerprint=_hash(seed)
    )


def _make_evaluation(session, candidate, revision: int = 1, **overrides):
    """Create a minimal candidate evaluation row for the given session."""
    fields = {
        'session': session,
        'session_revision': revision,
        'candidate': candidate,
        'candidate_fingerprint': _hash(100),
        'eligible': True,
        'requirements_hash': _hash(101),
        'policy': session.policy,
        'evaluation_hash': _hash(102),
        'evaluated_at': timezone.now(),
    }
    fields.update(overrides)
    return PartCandidateEvaluation.objects.create(**fields)


class VerificationModelDiscoveryTests(TestCase):
    """The verification aggregate registers with the part app registry."""

    def test_models_resolve_via_app_registry(self):
        """All nine verification models resolve via apps.get_model."""
        for name in MODEL_NAMES:
            model = apps.get_model('part', name)
            self.assertEqual(model.__name__, name)
            self.assertEqual(model._meta.app_label, 'part')

    def test_custom_permission_rows_exist(self):
        """Migrations created the custom Permission rows on the part app."""
        for codename in CUSTOM_PERMISSION_CODENAMES:
            permission = Permission.objects.get(
                codename=codename, content_type__app_label='part'
            )
            self.assertEqual(permission.codename, codename)


class PolicyVersionModelTests(TestCase):
    """Uniqueness and immutability rules of policy versions."""

    def test_unique_key_version(self):
        """Only one row may exist per (key, version) pair."""
        _make_policy(key='dup-key', version=1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            _make_policy(key='dup-key', version=1)

        # A different version of the same key remains legal
        other = _make_policy(key='dup-key', version=2)
        self.assertEqual(other.version, 2)

    def test_definition_mutable_while_draft(self):
        """A draft policy version accepts definition rewrites."""
        policy = _make_policy(key='draft-key')

        changed = copy.deepcopy(VALID_DEFINITION)
        changed['description'] = 'rewritten while draft'
        policy.definition = changed
        policy.save()

        policy.refresh_from_db()
        self.assertEqual(policy.status, 'draft')
        self.assertEqual(policy.definition['description'], 'rewritten while draft')

    def test_definition_immutable_after_activation(self):
        """Once activated, saving a changed definition raises ValidationError."""
        policy = _make_policy(key='active-key')
        activate_policy(policy)

        policy.refresh_from_db()
        self.assertEqual(policy.status, 'active')

        policy.definition = {**copy.deepcopy(VALID_DEFINITION), 'description': 'nope'}
        with self.assertRaises(ValidationError):
            policy.save()

        policy.refresh_from_db()
        self.assertEqual(policy.definition, VALID_DEFINITION)

    def test_revoke_preserves_definition(self):
        """Revocation flips status but never rewrites the definition."""
        policy = _make_policy(key='revoke-key')
        activate_policy(policy)

        revoke_policy(policy)

        policy.refresh_from_db()
        self.assertEqual(policy.status, 'revoked')
        self.assertIsNotNone(policy.effective_until)
        self.assertEqual(policy.definition, VALID_DEFINITION)


class SessionModelTests(TestCase):
    """Reference assignment and required relations of sessions."""

    @classmethod
    def setUpTestData(cls):
        """Create the shared draft policy version."""
        cls.policy = _make_policy(key='session-key')

    def test_reference_auto_assigned(self):
        """New sessions receive a unique 'PVS-%06d' reference."""
        first = _make_session(self.policy, seed=1)
        second = _make_session(self.policy, seed=2)

        self.assertEqual(first.reference, f'PVS-{first.pk:06d}')
        self.assertEqual(second.reference, f'PVS-{second.pk:06d}')
        self.assertNotEqual(first.reference, second.reference)

        first.refresh_from_db()
        self.assertEqual(first.reference, f'PVS-{first.pk:06d}')

    def test_policy_is_required(self):
        """Creating a session without a policy FK is rejected by the DB."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            PartVerificationSession.objects.create(
                purpose='manual', scope_fingerprint=_hash(3)
            )


class RequirementModelTests(TestCase):
    """Uniqueness of requirements within one session."""

    @classmethod
    def setUpTestData(cls):
        """Create a policy and two sessions to test key scoping."""
        cls.policy = _make_policy(key='requirement-key')
        cls.session = _make_session(cls.policy, seed=1)
        cls.other_session = _make_session(cls.policy, seed=2)

    def test_unique_session_key(self):
        """Requirement keys are unique per session, not globally."""
        PartVerificationRequirement.objects.create(
            session=self.session,
            key='electrical.phase',
            value_kind='decimal',
            operator='eq',
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            PartVerificationRequirement.objects.create(
                session=self.session,
                key='electrical.phase',
                value_kind='decimal',
                operator='eq',
            )

        # The same key on a different session remains legal
        row = PartVerificationRequirement.objects.create(
            session=self.other_session,
            key='electrical.phase',
            value_kind='decimal',
            operator='eq',
        )
        self.assertEqual(row.key, 'electrical.phase')


class CandidateEvaluationModelTests(TestCase):
    """Uniqueness and rank invariants of candidate evaluations."""

    @classmethod
    def setUpTestData(cls):
        """Create a session and candidate parts."""
        cls.policy = _make_policy(key='evaluation-key')
        cls.session = _make_session(cls.policy)
        cls.candidate = Part.objects.create(
            name='RPF Model Eval Candidate',
            IPN='RPF-MDL-001',
            active=True,
            component=True,
        )
        cls.other_candidate = Part.objects.create(
            name='RPF Model Eval Candidate B',
            IPN='RPF-MDL-002',
            active=True,
            component=True,
        )

    def test_unique_session_revision_candidate(self):
        """One evaluation row per (session, session_revision, candidate)."""
        _make_evaluation(self.session, self.candidate, revision=1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            _make_evaluation(self.session, self.candidate, revision=1)

        # A new revision re-evaluates the same candidate with a fresh row
        row = _make_evaluation(self.session, self.candidate, revision=2)
        self.assertEqual(row.session_revision, 2)

    def test_ineligible_candidate_cannot_hold_rank(self):
        """The check constraint forbids ranks on ineligible candidates."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            _make_evaluation(
                self.session, self.candidate, revision=1, eligible=False, rank=1
            )

        # Both legal shapes: eligible+ranked, ineligible+unranked
        ranked = _make_evaluation(
            self.session, self.candidate, revision=2, eligible=True, rank=1
        )
        excluded = _make_evaluation(
            self.session, self.other_candidate, revision=2, eligible=False, rank=None
        )
        self.assertEqual(ranked.rank, 1)
        self.assertIsNone(excluded.rank)


class DecisionModelTests(TestCase):
    """Kind/selection invariants and hash uniqueness of decisions."""

    @classmethod
    def setUpTestData(cls):
        """Create a session, candidate, evaluation, and deciding user."""
        cls.user = get_user_model().objects.create_user(
            username='rpf_models_decider', password='x'
        )
        cls.policy = _make_policy(key='decision-key')
        cls.session = _make_session(cls.policy)
        cls.candidate = Part.objects.create(
            name='RPF Model Decision Candidate',
            IPN='RPF-MDL-101',
            active=True,
            component=True,
        )
        cls.evaluation = _make_evaluation(cls.session, cls.candidate)

    def _make_decision(self, kind, seed, **overrides):
        """Create a decision row with the given kind and hash seed."""
        fields = {
            'session': self.session,
            'session_revision': 1,
            'kind': kind,
            'decision_hash': _hash(seed),
            'policy': self.policy,
            'decided_by': self.user,
            'reason': 'model test decision',
            'decided_at': timezone.now(),
        }
        fields.update(overrides)
        return PartVerificationDecision.objects.create(**fields)

    def test_confirmed_requires_selected_part(self):
        """A confirmed decision with a null selected part is rejected."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._make_decision(
                'confirmed',
                seed=201,
                selected_evaluation=self.evaluation,
                selected_part=None,
            )

    def test_no_safe_match_forbids_selected_part(self):
        """A no-safe-match decision carrying a selection is rejected."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._make_decision(
                'no_safe_match',
                seed=202,
                selected_evaluation=self.evaluation,
                selected_part=self.candidate,
            )

    def test_decision_hash_unique(self):
        """Two decisions may never share one decision hash."""
        self._make_decision(
            'confirmed',
            seed=203,
            selected_evaluation=self.evaluation,
            selected_part=self.candidate,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._make_decision('no_safe_match', seed=203)

    def test_valid_shapes_accepted(self):
        """Both legal decision shapes persist without error."""
        confirmed = self._make_decision(
            'confirmed',
            seed=204,
            selected_evaluation=self.evaluation,
            selected_part=self.candidate,
        )
        no_match = self._make_decision('no_safe_match', seed=205)
        self.assertEqual(confirmed.kind, 'confirmed')
        self.assertIsNone(no_match.selected_part_id)


class UseModelTests(TestCase):
    """Replay-safe uniqueness of decision uses."""

    @classmethod
    def setUpTestData(cls):
        """Create a confirmed decision to bind uses against."""
        cls.user = get_user_model().objects.create_user(
            username='rpf_models_user', password='x'
        )
        cls.policy = _make_policy(key='use-key')
        cls.session = _make_session(cls.policy)
        cls.candidate = Part.objects.create(
            name='RPF Model Use Candidate',
            IPN='RPF-MDL-201',
            active=True,
            component=True,
        )
        cls.evaluation = _make_evaluation(cls.session, cls.candidate)
        cls.decision = PartVerificationDecision.objects.create(
            session=cls.session,
            session_revision=1,
            kind='confirmed',
            selected_evaluation=cls.evaluation,
            selected_part=cls.candidate,
            decision_hash=_hash(301),
            policy=cls.policy,
            decided_by=cls.user,
            reason='model test decision',
            decided_at=timezone.now(),
        )

    def test_unique_consumer_kind_action_idempotency_key(self):
        """One use row per (consumer_kind, consumer_action, idempotency_key)."""
        PartVerificationUse.objects.create(
            decision=self.decision,
            consumer_kind='job_kit',
            consumer_action='substitution_decide',
            idempotency_key='use-1',
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            PartVerificationUse.objects.create(
                decision=self.decision,
                consumer_kind='job_kit',
                consumer_action='substitution_decide',
                idempotency_key='use-1',
            )

        # A different action with the same key remains legal
        row = PartVerificationUse.objects.create(
            decision=self.decision,
            consumer_kind='job_kit',
            consumer_action='substitution_apply',
            idempotency_key='use-1',
        )
        self.assertEqual(row.consumer_action, 'substitution_apply')


class CommandModelTests(TestCase):
    """Idempotency uniqueness of the command ledger."""

    def test_unique_command_idempotency_key(self):
        """One ledger row per (command, idempotency_key) pair."""
        PartVerificationCommand.objects.create(
            command='RPF_CREATE_SESSION',
            idempotency_key='cmd-1',
            request_hash=_hash(401),
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            PartVerificationCommand.objects.create(
                command='RPF_CREATE_SESSION',
                idempotency_key='cmd-1',
                request_hash=_hash(402),
            )

        # The same key under a different command remains legal
        row = PartVerificationCommand.objects.create(
            command='RPF_EVALUATE',
            idempotency_key='cmd-1',
            request_hash=_hash(403),
        )
        self.assertEqual(row.command, 'RPF_EVALUATE')


class ProtectedDeleteTests(TestCase):
    """PROTECT foreign keys keep referenced catalog rows undeletable."""

    def test_deleting_evaluated_part_is_protected(self):
        """Deleting a Part referenced by an evaluation raises ProtectedError."""
        policy = _make_policy(key='protect-key')
        session = _make_session(policy)

        # Inactive so Part.delete() reaches the database instead of refusing
        candidate = Part.objects.create(
            name='RPF Model Protected Candidate',
            IPN='RPF-MDL-301',
            active=False,
            component=True,
        )
        _make_evaluation(session, candidate)

        with self.assertRaises(ProtectedError):
            candidate.delete()

        self.assertTrue(Part.objects.filter(pk=candidate.pk).exists())
