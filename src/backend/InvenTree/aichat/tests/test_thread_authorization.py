"""Authorization and exact-replay tests for the chat repository."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from aichat.models import MessageRole, ThreadNamespace, TurnModality
from aichat.services import (
    AnonymousActorRejected,
    IdempotencyConflict,
    ScopedThreadRejected,
    ThreadNotFound,
    ThreadRepository,
    canonical_request_fingerprint,
)


class ThreadAuthorizationTests(TestCase):
    """Prove every operation is owner-, scope-, and namespace-bound."""

    def setUp(self) -> None:
        """Create two independent principals and repository boundaries."""
        users = get_user_model().objects
        self.owner = users.create_user(username='chat-owner')
        self.other = users.create_user(username='chat-other')
        self.repository = ThreadRepository(self.owner.pk, 'site:main')
        self.thread, _ = self.repository.get_or_create(title='Original')

    def _begin(self, *, key: str = 'typed-turn:one', content: str = 'Hello'):
        """Begin a normalized text turn with its canonical fingerprint."""
        fingerprint = canonical_request_fingerprint(
            content=content,
            modality=TurnModality.TEXT,
            trusted_context={'policy': 'one'},
        )
        result = self.repository.begin_turn(
            self.thread.pk,
            content=content,
            modality=TurnModality.TEXT,
            trusted_context={'policy': 'one'},
            modality_metadata={},
            idempotency_key=key,
            request_fingerprint=fingerprint,
            correlation_id='corr-one',
        )
        return result, fingerprint

    def test_anonymous_actor_is_rejected_but_resolved_scalar_is_accepted(self) -> None:
        """Only a server-resolved user id or authenticated user reaches storage."""
        for actor in (None, '', AnonymousUser()):
            with self.subTest(actor=actor):
                with self.assertRaises(AnonymousActorRejected):
                    ThreadRepository(actor, 'site:main')

        scalar_repository = ThreadRepository(self.owner.pk, 'site:main')
        self.assertEqual(scalar_repository.get(self.thread.pk), self.thread)

    def test_cross_owner_and_scope_lookups_do_not_enumerate(self) -> None:
        """A leaked id has the same not-found result outside either boundary."""
        repositories = (
            ThreadRepository(self.other.pk, 'site:main'),
            ThreadRepository(self.owner.pk, 'site:other'),
        )
        for repository in repositories:
            with self.subTest(repository=repository):
                self.assertEqual(repository.list(), [])
                with self.assertRaises(ThreadNotFound):
                    repository.get(self.thread.pk)
                with self.assertRaises(ThreadNotFound):
                    repository.get_or_create(self.thread.pk)
                with self.assertRaises(ThreadNotFound):
                    repository.rename(self.thread.pk, 'Leaked')
                with self.assertRaises(ThreadNotFound):
                    repository.append(
                        self.thread.pk,
                        role=MessageRole.USER,
                        content='Leaked',
                    )
                with self.assertRaises(ThreadNotFound):
                    repository.delete(self.thread.pk)

    def test_legacy_repository_rejects_scoped_identifier(self) -> None:
        """The scoped_ prefix stays reserved after the rail's removal (S14c).

        A stale scoped identifier must fail closed rather than resolve —
        or be creatable — inside the main namespace.
        """
        with self.assertRaises(ScopedThreadRejected):
            self.repository.get('scoped_thread_0000stale0000')
        with self.assertRaises(ScopedThreadRejected):
            self.repository.get_or_create('scoped_thread_0000stale0000')

    def test_append_replay_rename_and_delete_stay_in_boundary(self) -> None:
        """Transcript and lifecycle operations work only inside the boundary."""
        message = self.repository.append(
            self.thread.pk,
            role=MessageRole.SYSTEM,
            content='Policy context',
        )
        begun, fingerprint = self._begin()
        terminal = self.repository.terminal(
            begun.turn.pk,
            state='complete',
            canonical_result={'kind': 'answer', 'response_state': 'complete'},
            output_content='Hello back',
        )
        replay = self.repository.replay(
            self.thread.pk,
            idempotency_key='typed-turn:one',
            request_fingerprint=fingerprint,
        )
        renamed = self.repository.rename(self.thread.pk, 'Renamed')
        transcript = self.repository.messages(self.thread.pk)

        self.assertEqual(message.sequence, 1)
        self.assertEqual([item.sequence for item in transcript], [1, 2, 3])
        self.assertEqual(replay.pk, terminal.pk)
        self.assertEqual(replay.input_message.content, 'Hello')
        self.assertEqual(replay.output_message.content, 'Hello back')
        self.assertEqual(renamed.title, 'Renamed')

        self.repository.delete(self.thread.pk)
        with self.assertRaises(ThreadNotFound):
            self.repository.get(self.thread.pk)

    def test_exact_replay_and_changed_fingerprint_conflict(self) -> None:
        """Exact key reuse writes once while changed canonical input conflicts."""
        begun, fingerprint = self._begin()
        replayed, _ = self._begin()

        self.assertFalse(begun.replayed)
        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.turn.pk, begun.turn.pk)
        self.assertEqual(self.thread.turns.count(), 1)
        self.assertEqual(self.thread.messages.count(), 1)

        with self.assertRaises(IdempotencyConflict):
            self.repository.begin_turn(
                self.thread.pk,
                content='Changed',
                modality=TurnModality.TEXT,
                trusted_context={'policy': 'one'},
                modality_metadata={},
                idempotency_key='typed-turn:one',
                request_fingerprint='f' * 64,
                correlation_id='corr-two',
            )
        with self.assertRaises(IdempotencyConflict):
            self.repository.replay(
                self.thread.pk,
                idempotency_key='typed-turn:one',
                request_fingerprint='0' * 64,
            )
        self.assertEqual(
            self.repository.replay(
                self.thread.pk,
                idempotency_key='typed-turn:one',
                request_fingerprint=fingerprint,
            ).pk,
            begun.turn.pk,
        )

    def test_replay_does_not_disclose_turn_across_boundary(self) -> None:
        """Turn replay first authorizes its parent thread."""
        begun, _ = self._begin()
        for repository in (
            ThreadRepository(self.other.pk, 'site:main'),
            ThreadRepository(self.owner.pk, 'site:other'),
        ):
            with self.assertRaises(ThreadNotFound):
                repository.replay(
                    self.thread.pk,
                    idempotency_key='typed-turn:one',
                )
            with self.assertRaises(ThreadNotFound):
                repository.terminal(
                    begun.turn.pk,
                    state='failed',
                    canonical_result={
                        'kind': 'error',
                        'response_state': 'failed',
                    },
                    output_content='Failed',
                )
