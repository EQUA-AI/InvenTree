"""Production-database locking tests for turn idempotency and ordering."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from aichat.models import ChatMessage, ChatTurn, TurnModality
from aichat.services import ThreadRepository, canonical_request_fingerprint


class ThreadConcurrencyTests(TransactionTestCase):
    """Exercise row-lock behavior using independent database connections."""

    reset_sequences = True

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_same_idempotency_key_creates_one_durable_turn(self) -> None:
        """A production row lock serializes concurrent delivery of one turn."""
        user = get_user_model().objects.create_user(username='chat-concurrent')
        repository = ThreadRepository(user.pk, 'site:main')
        thread, _ = repository.get_or_create()
        fingerprint = canonical_request_fingerprint(
            content='Concurrent input',
            modality=TurnModality.TEXT,
            trusted_context={'policy': 'one'},
        )
        barrier = Barrier(2)

        def begin() -> tuple[str, bool]:
            close_old_connections()
            try:
                worker_repository = ThreadRepository(user.pk, 'site:main')
                barrier.wait()
                result = worker_repository.begin_turn(
                    thread.pk,
                    content='Concurrent input',
                    modality=TurnModality.TEXT,
                    trusted_context={'policy': 'one'},
                    modality_metadata={},
                    idempotency_key='typed-turn:concurrent',
                    request_fingerprint=fingerprint,
                    correlation_id='corr-concurrent',
                )
                return result.turn.pk, result.replayed
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: begin(), range(2)))

        self.assertEqual(len({turn_id for turn_id, _ in results}), 1)
        self.assertEqual(sorted(replayed for _, replayed in results), [False, True])
        self.assertEqual(ChatTurn.objects.filter(thread=thread).count(), 1)
        self.assertEqual(ChatMessage.objects.filter(thread=thread).count(), 1)
