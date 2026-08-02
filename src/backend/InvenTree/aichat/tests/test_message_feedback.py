"""The message-feedback ledger: owner-bound, closed vocabulary, upsert.

The thumbs previously died in React state; this ledger is the before/after
instrument for behaviour-changing releases (notably diagnosis turns becoming
refusals), so its boundaries matter: a foreign thread is indistinguishable
from a missing one, only assistant messages are ratable, and re-rating
updates in place rather than accumulating rows.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from aichat.models import ChatMessage, ChatThread, MessageFeedback
from aichat.services import feedback as feedback_service


def _thread(owner, suffix: str) -> ChatThread:
    return ChatThread.objects.create(
        owner=owner,
        scope_key='site:test',
        scope_hash='0' * 64,
        title=f'Thread {suffix}',
    )


def _message(thread: ChatThread, sequence: int, role: str = 'assistant') -> ChatMessage:
    return ChatMessage.objects.create(
        thread=thread, sequence=sequence, role=role, content=f'answer {sequence}'
    )


class MessageFeedbackServiceTests(TestCase):
    """record_feedback boundaries and upsert semantics."""

    @classmethod
    def setUpTestData(cls):
        """One owner with a thread; one stranger."""
        cls.owner = get_user_model().objects.create_user(
            username='fb-owner', email='o@example.com', password='pw'
        )
        cls.stranger = get_user_model().objects.create_user(
            username='fb-stranger', email='s@example.com', password='pw'
        )
        cls.thread = _thread(cls.owner, 'one')
        cls.user_msg = _message(cls.thread, 1, role='user')
        cls.answer = _message(cls.thread, 2)

    def test_rating_a_known_assistant_message_upserts(self):
        """A durable id binds directly; re-rating replaces, never duplicates."""
        row = feedback_service.record_feedback(
            owner=self.owner,
            thread_id=self.thread.pk,
            message_id=self.answer.pk,
            rating='up',
        )
        self.assertEqual(row.message_id, self.answer.pk)
        self.assertEqual(row.client_message_id, '')
        again = feedback_service.record_feedback(
            owner=self.owner,
            thread_id=self.thread.pk,
            message_id=self.answer.pk,
            rating='down',
            reason='wrong bin location',
        )
        self.assertEqual(MessageFeedback.objects.count(), 1)
        self.assertEqual(again.rating, 'down')
        self.assertEqual(again.reason, 'wrong bin location')

    def test_unknown_client_id_binds_latest_assistant_message(self):
        """A just-streamed rating lands on the newest answer, breadcrumbed."""
        newer = _message(self.thread, 3)
        row = feedback_service.record_feedback(
            owner=self.owner,
            thread_id=self.thread.pk,
            message_id='msg_1712_client_generated',
            rating='up',
        )
        self.assertEqual(row.message_id, newer.pk)
        self.assertEqual(row.client_message_id, 'msg_1712_client_generated')

    def test_foreign_thread_is_indistinguishable_from_missing(self):
        """A stranger rating the owner's thread gets the uniform refusal."""
        for owner, thread_id in (
            (self.stranger, self.thread.pk),
            (self.owner, 'thread_does_not_exist'),
        ):
            with self.assertRaises(feedback_service.FeedbackError) as caught:
                feedback_service.record_feedback(
                    owner=owner,
                    thread_id=thread_id,
                    message_id=self.answer.pk,
                    rating='up',
                )
            self.assertEqual(caught.exception.code, 'FEEDBACK_THREAD_UNAVAILABLE')

    def test_closed_rating_vocabulary_and_user_messages_unratable(self):
        """Bad ratings and user-role messages are refused with stable codes."""
        with self.assertRaises(feedback_service.FeedbackError) as caught:
            feedback_service.record_feedback(
                owner=self.owner,
                thread_id=self.thread.pk,
                message_id=self.answer.pk,
                rating='amazing',
            )
        self.assertEqual(caught.exception.code, 'FEEDBACK_INVALID_RATING')

        empty = _thread(self.owner, 'empty')
        only_user = _message(empty, 1, role='user')
        with self.assertRaises(feedback_service.FeedbackError) as caught:
            feedback_service.record_feedback(
                owner=self.owner,
                thread_id=empty.pk,
                message_id=only_user.pk,
                rating='up',
            )
        self.assertEqual(caught.exception.code, 'FEEDBACK_MESSAGE_UNAVAILABLE')

    def test_content_hash_binds_the_rated_message_not_the_latest(self):
        """Rating an OLDER streamed answer attributes by content, not recency.

        Freshly streamed messages carry client ids the server has never seen;
        without the content hash every verdict would collapse onto the newest
        answer and poison the instrument (adversarial review finding).
        """
        import hashlib

        newer = _message(self.thread, 4)
        digest = hashlib.sha256(self.answer.content.encode('utf-8')).hexdigest()
        row = feedback_service.record_feedback(
            owner=self.owner,
            thread_id=self.thread.pk,
            message_id='msg_9999_client_generated',
            rating='down',
            content_sha256=digest,
        )
        self.assertEqual(row.message_id, self.answer.pk)
        self.assertNotEqual(row.message_id, newer.pk)
        self.assertEqual(row.client_message_id, 'msg_9999_client_generated')

    def test_retraction_clears_the_row_idempotently(self):
        """Toggling a thumb off deletes the verdict; repeating is a no-op."""
        feedback_service.record_feedback(
            owner=self.owner,
            thread_id=self.thread.pk,
            message_id=self.answer.pk,
            rating='up',
        )
        cleared = feedback_service.clear_feedback(
            owner=self.owner, thread_id=self.thread.pk, message_id=self.answer.pk
        )
        self.assertTrue(cleared)
        self.assertEqual(MessageFeedback.objects.count(), 0)
        again = feedback_service.clear_feedback(
            owner=self.owner, thread_id=self.thread.pk, message_id=self.answer.pk
        )
        self.assertFalse(again)

    def test_scoped_namespace_threads_are_not_ratable(self):
        """Feedback targets the unscoped drawer only; scoped threads refuse."""
        from aichat.models import ThreadNamespace

        scoped = ChatThread.objects.create(
            id='scoped_feedback_probe_thread',
            owner=self.owner,
            scope_key='site:test',
            scope_hash='0' * 64,
            namespace=ThreadNamespace.SCOPED,
            title='scoped',
        )
        with self.assertRaises(feedback_service.FeedbackError) as caught:
            feedback_service.record_feedback(
                owner=self.owner,
                thread_id=scoped.pk,
                message_id='anything',
                rating='up',
            )
        self.assertEqual(caught.exception.code, 'FEEDBACK_THREAD_UNAVAILABLE')
