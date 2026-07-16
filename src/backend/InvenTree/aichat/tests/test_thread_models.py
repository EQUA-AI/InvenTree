"""Model and portability tests for durable AI chat persistence."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from aichat.models import ChatMessage, ChatThread, ChatTurn, MessageRole, TurnModality
from aichat.services import ThreadRepository, canonical_request_fingerprint


class ChatThreadModelTests(TestCase):
    """Prove stable identifiers, ownership, indexes, and scalar constraints."""

    def setUp(self) -> None:
        """Create a durable owner for each test."""
        self.user = get_user_model().objects.create_user(username='chat-model-owner')
        self.repository = ThreadRepository(self.user.pk, 'site:main')

    def test_stable_ids_scope_hash_and_portable_json(self) -> None:
        """Stable ids and plain JSON fields survive a database round trip."""
        thread, created = self.repository.get_or_create()
        fingerprint = canonical_request_fingerprint(
            content='Inspect pump',
            modality=TurnModality.VOICE,
            trusted_context={'policy_version': '1'},
            modality_metadata={'language': 'en-US'},
        )
        begun = self.repository.begin_turn(
            thread.pk,
            content='Inspect pump',
            modality=TurnModality.VOICE,
            trusted_context={'policy_version': '1'},
            modality_metadata={'language': 'en-US'},
            idempotency_key='voice-turn:one',
            request_fingerprint=fingerprint,
            correlation_id='corr-one',
        )
        turn = self.repository.terminal(
            begun.turn.pk,
            state='complete',
            canonical_result={'kind': 'repair_diagnosis', 'response_version': 1},
            output_content='Inspect the bearing.',
            workflow_id='wf1',
        )

        self.assertTrue(created)
        self.assertTrue(thread.pk.startswith('thread_'))
        self.assertTrue(turn.pk.startswith('turn_'))
        self.assertTrue(turn.input_message_id.startswith('message_'))
        self.assertTrue(turn.output_message_id.startswith('message_'))
        self.assertEqual(len(thread.scope_hash), 64)
        self.assertEqual(turn.trusted_context, {'policy_version': '1'})
        self.assertEqual(turn.modality_metadata, {'language': 'en-US'})
        self.assertEqual(turn.canonical_result['response_version'], 1)
        thread.refresh_from_db()
        self.assertEqual(thread.last_workflow, 'wf1')

    def test_owner_is_non_null_and_protected(self) -> None:
        """A thread can neither omit nor silently lose its owner."""
        thread, _ = self.repository.get_or_create()
        with self.assertRaises(ProtectedError):
            self.user.delete()
        self.assertTrue(ChatThread.objects.filter(pk=thread.pk).exists())

    def test_message_sequence_and_turn_key_are_unique_per_thread(self) -> None:
        """Database constraints backstop ordering and idempotency."""
        thread, _ = self.repository.get_or_create()
        message = self.repository.append(
            thread.pk,
            role=MessageRole.USER,
            content='First',
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChatMessage.objects.create(
                thread=thread,
                sequence=message.sequence,
                role=MessageRole.USER,
                content='Duplicate',
            )

        fingerprint = canonical_request_fingerprint(
            content='Second',
            modality=TurnModality.TEXT,
            trusted_context={},
        )
        turn = self.repository.begin_turn(
            thread.pk,
            content='Second',
            modality=TurnModality.TEXT,
            trusted_context={},
            modality_metadata={},
            idempotency_key='typed-turn:one',
            request_fingerprint=fingerprint,
            correlation_id='',
        ).turn
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChatTurn.objects.create(
                thread=thread,
                input_message=message,
                modality=TurnModality.TEXT,
                request_fingerprint='a' * 64,
                idempotency_key=turn.idempotency_key,
            )

    def test_owner_scope_namespace_indexes_have_explicit_intent(self) -> None:
        """Boundary and ordering query shapes retain named indexes."""
        thread_indexes = {index.name for index in ChatThread._meta.indexes}
        message_indexes = {index.name for index in ChatMessage._meta.indexes}
        turn_indexes = {index.name for index in ChatTurn._meta.indexes}

        self.assertIn('aichat_thread_boundary_idx', thread_indexes)
        self.assertIn('aichat_thread_scope_idx', thread_indexes)
        self.assertIn('aichat_message_order_idx', message_indexes)
        self.assertIn('aichat_turn_thread_time_idx', turn_indexes)
        self.assertIn('aichat_turn_state_time_idx', turn_indexes)
