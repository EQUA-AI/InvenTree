"""S10 WP-A5: owner/grant-safe evidence-set reads with live member reauth."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from aichat.models import TurnModality, TurnState
from aichat.services import (
    ThreadNotFound,
    ThreadRepository,
    canonical_request_fingerprint,
)


def _terminal_with_set(repository, thread, *, set_id, members, supports_expansion=True):
    """Create one COMPLETE turn carrying an evidence set."""
    fingerprint = canonical_request_fingerprint(
        content='q', modality=TurnModality.TEXT, trusted_context={}
    )
    turn = repository.begin_turn(
        thread.pk,
        content='q',
        modality=TurnModality.TEXT,
        trusted_context={},
        modality_metadata={},
        idempotency_key=f'turn:{set_id}',
        request_fingerprint=fingerprint,
        correlation_id='',
    ).turn
    repository.terminal(
        turn.pk,
        state=TurnState.COMPLETE,
        canonical_result={'detailed_response': 'answer', 'response_state': 'complete'},
        evidence_sets=[
            {
                'id': set_id,
                'source_class': 'work_order',
                'filters': {},
                'population_count': len(members),
                'evaluated_count': len(members),
                'displayed_count': len(members),
                'complete_population': True,
                'supports_expansion': supports_expansion,
                'member_cap': 25000,
                'calculation': {'operation': 'count', 'result': str(len(members))},
                'members': members,
            }
        ],
    )
    return turn


class EvidenceSetReadTests(TestCase):
    """The repository is the only read path; failures are indistinguishable."""

    def setUp(self) -> None:
        """One owner with a set, one stranger with their own boundary."""
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username='ev-owner')
        self.stranger = user_model.objects.create_user(username='ev-stranger')
        self.repository = ThreadRepository(self.owner.pk, 'site:main')
        self.thread, _ = self.repository.get_or_create()
        self.set_id = 'set_' + 'c' * 32
        _terminal_with_set(
            self.repository,
            self.thread,
            set_id=self.set_id,
            members=[(1, 'work_order', '41', ''), (2, 'unknown_class', '9', '')],
        )

    def test_owner_reads_header_and_members(self) -> None:
        """The owner resolves the set; members reauthorize per class."""
        row = self.repository.evidence_set(self.thread.pk, self.set_id)
        self.assertEqual(row.member_count, 2)

        fake_work_order = mock.Mock(reference='WO-0041', pk=41)
        with mock.patch(
            'tasks.ai_read.authorized_work_order', return_value=fake_work_order
        ):
            members = self.repository.evidence_set_members(
                self.thread.pk, self.set_id
            )
        self.assertEqual(members[0]['available'], True)
        self.assertEqual(members[0]['label'], 'WO-0041')
        self.assertEqual(members[0]['member_index'], 1)
        # Unknown source classes fail closed, indistinguishably.
        self.assertEqual(members[1]['available'], False)
        self.assertIsNone(members[1]['label'])
        self.assertIsNone(members[1]['source_object_id'])

    def test_revoked_member_is_indistinguishable_from_missing(self) -> None:
        """authorized_work_order returning None projects the same shape."""
        with mock.patch('tasks.ai_read.authorized_work_order', return_value=None):
            members = self.repository.evidence_set_members(
                self.thread.pk, self.set_id
            )
        self.assertEqual(members[0]['available'], False)
        self.assertIsNone(members[0]['label'])
        self.assertIsNone(members[0]['source_object_id'])

    def test_stranger_cannot_resolve_the_set(self) -> None:
        """Another actor's boundary yields the same not-found as no set."""
        stranger_repository = ThreadRepository(self.stranger.pk, 'site:main')
        with self.assertRaises(ThreadNotFound):
            stranger_repository.evidence_set(self.thread.pk, self.set_id)

    def test_cross_thread_set_id_is_not_found(self) -> None:
        """A real set id under a different thread resolves to nothing."""
        other_thread, _ = self.repository.get_or_create('thread_' + 'f' * 32)
        with self.assertRaises(ThreadNotFound):
            self.repository.evidence_set(other_thread.pk, self.set_id)

    def test_digest_only_set_never_expands(self) -> None:
        """supports_expansion=False means expansion was never promised."""
        digest_id = 'set_' + 'd' * 32
        _terminal_with_set(
            self.repository,
            self.thread,
            set_id=digest_id,
            members=[],
            supports_expansion=False,
        )
        # The header stays readable; the member listing 404s generically.
        row = self.repository.evidence_set(self.thread.pk, digest_id)
        self.assertFalse(row.supports_expansion)
        with self.assertRaises(ThreadNotFound):
            self.repository.evidence_set_members(self.thread.pk, digest_id)

    def test_pagination_uses_after_ordinal(self) -> None:
        """after_ordinal + limit slice the ordered membership."""
        many_id = 'set_' + 'e' * 32
        _terminal_with_set(
            self.repository,
            self.thread,
            set_id=many_id,
            members=[(index, 'work_order', str(index), '') for index in range(1, 8)],
        )
        with mock.patch('tasks.ai_read.authorized_work_order', return_value=None):
            page = self.repository.evidence_set_members(
                self.thread.pk, many_id, after_ordinal=3, limit=2
            )
        self.assertEqual([member['member_index'] for member in page], [4, 5])
