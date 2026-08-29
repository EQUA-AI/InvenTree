"""S10 WP-A4: evidence-set models + the atomic terminal write."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from aichat.models import (
    ChatEvidenceSet,
    ChatEvidenceSetMember,
    ChatTurn,
    TurnModality,
    TurnState,
)
from aichat.services import (
    InvalidBoundary,
    ThreadRepository,
    canonical_request_fingerprint,
)


def _canonical_result() -> dict:
    return {
        'kind': 'evidence_analysis',
        'response_version': 2,
        'response_state': 'complete',
        'detailed_response': '2 matching records were found. [1]',
        'spoken_summary': '',
    }


def _set_spec(**overrides) -> dict:
    spec = {
        'id': 'set_' + 'a' * 32,
        'source_class': 'work_order',
        'filters': {'machine_ids': [12]},
        'population_count': 2,
        'evaluated_count': 2,
        'displayed_count': 2,
        'complete_population': True,
        'high_watermarks': {'updated_at': '2026-08-27T00:00:00+00:00'},
        'snapshot_hash': 'snap_test',
        'supports_expansion': True,
        'member_cap': 25000,
        'calculation': {'operation': 'count', 'result': '2'},
        'members': [(1, 'work_order', '41', 'v3'), (2, 'work_order', '42', '')],
        'authorization_scope_hash': 'authhash',
        'analysis_scope_hash': 'scopehash',
    }
    spec.update(overrides)
    return spec


class EvidenceSetTerminalWriteTests(TestCase):
    """Membership rides ThreadRepository.terminal()'s transaction."""

    def setUp(self) -> None:
        """Create an owner-bound repository and thread."""
        user = get_user_model().objects.create_user(username='evidence-owner')
        self.repository = ThreadRepository(user.pk, 'site:main')
        self.thread, _ = self.repository.get_or_create()

    def _begin_turn(self, key: str = 'turn:evidence-1') -> ChatTurn:
        """Open one RUNNING turn to terminate in the test."""
        fingerprint = canonical_request_fingerprint(
            content='How many open work orders?',
            modality=TurnModality.TEXT,
            trusted_context={},
        )
        result = self.repository.begin_turn(
            self.thread.pk,
            content='How many open work orders?',
            modality=TurnModality.TEXT,
            trusted_context={},
            modality_metadata={},
            idempotency_key=key,
            request_fingerprint=fingerprint,
            correlation_id='corr-evidence',
        )
        return result.turn

    def test_terminal_persists_sets_and_members_atomically(self) -> None:
        """The happy path writes the set + ordered members with the turn."""
        turn = self._begin_turn()
        self.repository.terminal(
            turn.pk,
            state=TurnState.COMPLETE,
            canonical_result=_canonical_result(),
            workflow_id='analysis_executor',
            evidence_sets=[_set_spec()],
        )
        evidence_set = ChatEvidenceSet.objects.get(turn=turn)
        self.assertEqual(evidence_set.pk, 'set_' + 'a' * 32)
        self.assertEqual(evidence_set.member_count, 2)
        self.assertTrue(evidence_set.complete_population)
        self.assertEqual(evidence_set.authorization_scope_hash, 'authhash')
        members = list(evidence_set.members.all())
        self.assertEqual([member.ordinal for member in members], [1, 2])
        self.assertEqual(members[0].source_object_id, '41')
        self.assertEqual(members[0].source_version, 'v3')

    def test_failed_member_write_rolls_back_the_terminal_row(self) -> None:
        """A poisoned second spec must undo the first set AND the terminal."""
        turn = self._begin_turn()
        poisoned = _set_spec(
            id='set_' + 'b' * 32,
            members=[(1, 'work_order', '41', ''), (3, 'work_order', '42', '')],
        )
        with self.assertRaises(InvalidBoundary):
            self.repository.terminal(
                turn.pk,
                state=TurnState.COMPLETE,
                canonical_result=_canonical_result(),
                evidence_sets=[_set_spec(), poisoned],
            )
        turn.refresh_from_db()
        self.assertEqual(turn.state, TurnState.RUNNING)
        self.assertIsNone(turn.output_message)
        self.assertEqual(ChatEvidenceSet.objects.count(), 0)
        self.assertEqual(ChatEvidenceSetMember.objects.count(), 0)

    def test_replayed_terminal_never_duplicates_sets(self) -> None:
        """The idempotent replay path returns before the evidence write."""
        turn = self._begin_turn()
        for _ in range(2):
            self.repository.terminal(
                turn.pk,
                state=TurnState.COMPLETE,
                canonical_result=_canonical_result(),
                evidence_sets=[_set_spec()],
            )
        self.assertEqual(ChatEvidenceSet.objects.count(), 1)
        self.assertEqual(ChatEvidenceSetMember.objects.count(), 2)

    def test_membership_over_cap_is_refused(self) -> None:
        """A spec exceeding its own cap refuses before any row exists."""
        turn = self._begin_turn()
        overfull = _set_spec(
            member_cap=1,
            members=[(1, 'work_order', '41', ''), (2, 'work_order', '42', '')],
        )
        with self.assertRaises(InvalidBoundary):
            self.repository.terminal(
                turn.pk,
                state=TurnState.COMPLETE,
                canonical_result=_canonical_result(),
                evidence_sets=[overfull],
            )
        turn.refresh_from_db()
        self.assertEqual(turn.state, TurnState.RUNNING)

    def test_thread_purge_cascades_through_sets_and_members(self) -> None:
        """Immediate thread deletion removes every evidence row."""
        turn = self._begin_turn()
        self.repository.terminal(
            turn.pk,
            state=TurnState.COMPLETE,
            canonical_result=_canonical_result(),
            evidence_sets=[_set_spec()],
        )
        self.thread.delete()
        self.assertEqual(ChatEvidenceSet.objects.count(), 0)
        self.assertEqual(ChatEvidenceSetMember.objects.count(), 0)


class EvidenceSetConstraintTests(TestCase):
    """The DB itself enforces cap and ordinal uniqueness (Migration 3)."""

    def setUp(self) -> None:
        """Create one RUNNING turn for direct model-level writes."""
        user = get_user_model().objects.create_user(username='evidence-db')
        repository = ThreadRepository(user.pk, 'site:main')
        self.thread, _ = repository.get_or_create()
        fingerprint = canonical_request_fingerprint(
            content='x', modality=TurnModality.TEXT, trusted_context={}
        )
        self.turn = repository.begin_turn(
            self.thread.pk,
            content='x',
            modality=TurnModality.TEXT,
            trusted_context={},
            modality_metadata={},
            idempotency_key='turn:constraints',
            request_fingerprint=fingerprint,
            correlation_id='',
        ).turn

    def _bare_set(self, **overrides) -> ChatEvidenceSet:
        """Create a minimal set row with overridable fields."""
        fields = {
            'turn': self.turn,
            'source_class': 'work_order',
            'population_count': 1,
            'evaluated_count': 1,
            'complete_population': True,
        }
        fields.update(overrides)
        return ChatEvidenceSet.objects.create(**fields)

    def test_member_count_above_cap_violates_check_constraint(self) -> None:
        """member_count <= member_cap is a DB-level invariant."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._bare_set(member_count=2, member_cap=1)

    def test_cap_above_envelope_violates_check_constraint(self) -> None:
        """No row may raise its cap above the adopted envelope."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._bare_set(member_cap=25001)

    def test_duplicate_ordinal_violates_unique_constraint(self) -> None:
        """(set, ordinal) uniqueness is a DB-level invariant."""
        evidence_set = self._bare_set()
        ChatEvidenceSetMember.objects.create(
            set=evidence_set, ordinal=1, source_class='work_order', source_object_id='1'
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChatEvidenceSetMember.objects.create(
                set=evidence_set,
                ordinal=1,
                source_class='work_order',
                source_object_id='2',
            )
