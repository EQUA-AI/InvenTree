"""M1 (GR-31 seat 1): ``ThreadRepository.recall_window`` on the real database.

The island exercises the statement on SQLite; this suite runs it under the
Django runner (PostgreSQL in the dev container and on the fork-postgres CI
lane) so the annotated join is proven on the engine that serves.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from aichat.models import ChatThread, MessageRole
from aichat.services.threads import ThreadRepository


class RecallWindowTests(TestCase):
    """One statement, boundary in SQL, summary riding on every row."""

    def setUp(self):
        """One owner with a thread of six messages and a compacted summary."""
        self.user = get_user_model().objects.create_user(username='recall-owner')
        self.repository = ThreadRepository(self.user.pk, 'site:main')
        self.thread, _ = self.repository.get_or_create(title='Recall')
        for index in range(6):
            self.repository.append(
                self.thread.pk,
                role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
                content=f'row {index + 1}',
            )
        ChatThread.objects.filter(pk=self.thread.pk).update(
            summary='Pump 3 diagnosis', summary_through_sequence=2
        )

    def test_one_query_returns_window_and_summary(self):
        """The window, the summary, the watermark and next_sequence in one query."""
        with self.assertNumQueries(1):
            window = self.repository.recall_window(
                self.thread.pk, limit=12, exclude_latest=1
            )
        self.assertEqual([row.sequence for row in window.rows], [1, 2, 3, 4, 5])
        self.assertEqual(window.rows[0].content, 'row 1')
        self.assertEqual(window.rows[-1].role, 'user')
        self.assertEqual(window.summary, 'Pump 3 diagnosis')
        self.assertEqual(window.watermark, 2)
        self.assertEqual(window.next_sequence, 7)
        self.assertEqual(window.db_round_trips, 1)

    def test_limit_bounds_the_window_in_sql(self):
        """Only the newest ``limit`` rows after the exclusion are fetched."""
        window = self.repository.recall_window(
            self.thread.pk, limit=2, exclude_latest=1
        )
        self.assertEqual([row.sequence for row in window.rows], [4, 5])
        self.assertEqual(
            self.repository.recall_window(self.thread.pk, limit=0).rows, ()
        )

    def test_boundary_is_applied_in_the_statement(self):
        """Another owner in the same scope sees an empty window, never the summary."""
        other = get_user_model().objects.create_user(username='recall-other')
        stranger = ThreadRepository(other.pk, 'site:main')
        with self.assertNumQueries(1):
            window = stranger.recall_window(self.thread.pk, limit=12)
        self.assertEqual(window.rows, ())
        self.assertEqual(window.summary, '')
        self.assertEqual(window.watermark, 0)

    def test_scoped_ids_are_refused(self):
        """The retired scoped-rail id prefix fails closed here too."""
        from aichat.services import ScopedThreadRejected

        with self.assertRaises(ScopedThreadRejected):
            self.repository.recall_window('scoped_abc', limit=5)
