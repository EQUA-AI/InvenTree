"""S16 A7: the retrieval ledger records outcomes and never breaks the search."""

from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, tag

from ai.core.config import Settings
from ai.core.integrations.controlled_document_corpus import search_corpus
from aichat.models import RetrievalMiss


class _Embedder:
    def embed_batch(self, inputs):
        return [[0.5] * 8 for _ in inputs]


class _Search:
    def __init__(self, rows=None):
        self.rows = rows or []

    def search(self, **kwargs):
        return list(self.rows)


def _settings():
    return Settings(_env_file=None, single_site_policy_key='site-under-test')


class RetrievalMissLedgerTests(TestCase):
    """Every corpus search writes exactly one metadata-only ledger row."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username='ledger-reader', password='x'
        )

    def _search(self, *, rows=None, **kwargs):
        with mock.patch('ai.core.config.get_settings', return_value=_settings()):
            return search_corpus(
                user=self.user,
                query=kwargs.pop('query', 'How do I calibrate the flux capacitor?'),
                search_client=_Search(rows),
                embedding_client=_Embedder(),
                embedding_dimensions=8,
                **kwargs,
            )

    def test_zero_hit_search_writes_exactly_one_row_without_answer_text(self):
        result = self._search()
        self.assertEqual(result['returned_count'], 0)
        rows = list(RetrievalMiss.objects.all())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.hit_count, 0)
        self.assertIsNone(row.top_score)
        self.assertEqual(row.query, 'How do I calibrate the flux capacitor?')
        self.assertEqual(row.scope_key, 'site-under-test')
        self.assertEqual(row.user_id, self.user.pk)
        # The row must carry nothing beyond the question and its outcome —
        # every persisted field is enumerated here so a chunk/answer field
        # cannot be added without failing this test.
        persisted = {
            field.name
            for field in RetrievalMiss._meta.get_fields()
            if getattr(field, 'concrete', False)
        }
        self.assertEqual(
            persisted,
            {
                'id',
                'user',
                'query',
                'hit_count',
                'top_score',
                'machine_filter',
                'document_class',
                'scope_key',
                'corpus',
                'part_filter',
                # S5 shadow evidence: scope identity + enforcement outcome —
                # content-free coordinates, never query or answer text.
                'scope_hash',
                'scope_mode',
                'scope_enforced',
                'out_of_scope_hits',
                'created_at',
            },
        )
        # Governed searches keep the pre-R2 defaults on the new columns.
        self.assertEqual(row.corpus, 'governed')
        self.assertEqual(row.part_filter, '')

    def test_hit_search_records_count_and_top_score(self):
        rows = [
            {'chunk': 'Torque to 45 Nm', '@search.score': 2.5, 'document_id': 'd'},
            {'chunk': 'Use thread locker', '@search.score': 1.5, 'document_id': 'd'},
        ]
        result = self._search(rows=rows)
        self.assertEqual(result['returned_count'], 2)
        row = RetrievalMiss.objects.get()
        self.assertEqual(row.hit_count, 2)
        self.assertEqual(row.top_score, 2.5)
        self.assertNotIn('Torque', row.query)

    def test_ambiguous_machine_resolution_is_recorded(self):
        result = self._search(
            machine='pump',
            machine_resolver=lambda actor, name: [
                {'machine_id': 1, 'name': 'Pump A', 'serial': 'A'},
                {'machine_id': 2, 'name': 'Pump B', 'serial': 'B'},
            ],
        )
        self.assertEqual(result['machine_filter'], 'ambiguous')
        row = RetrievalMiss.objects.get()
        self.assertEqual(row.machine_filter, 'ambiguous')
        self.assertEqual(row.hit_count, 0)

    def test_attachment_corpus_rows_carry_their_surface(self):
        """R2: the attachment tool's rows stay separable from governed ones."""
        from aichat.services.retrieval_misses import record_search

        record_search(
            user=self.user,
            query='What is the gasket shelf life?',
            hit_count=0,
            top_score=None,
            machine_filter='not_requested',
            document_class='datasheet',
            scope_key='site-under-test',
            corpus='attachment',
            part_filter='ambiguous',
        )
        row = RetrievalMiss.objects.get()
        self.assertEqual(row.corpus, 'attachment')
        self.assertEqual(row.part_filter, 'ambiguous')
        self.assertEqual(row.document_class, 'datasheet')

    def test_rollup_corpus_option_slices_one_surface(self):
        """--corpus restricts totals and rows to that retrieval surface."""
        from aichat.services.retrieval_misses import record_search

        self._search(query='Governed miss')
        record_search(
            user=self.user,
            query='Attachment miss',
            hit_count=0,
            top_score=None,
            machine_filter='not_requested',
            document_class=None,
            scope_key='site-under-test',
            corpus='attachment',
        )
        out = StringIO()
        call_command('retrieval_misses', '--json', '--corpus', 'attachment', stdout=out)
        import json

        report = json.loads(out.getvalue())
        self.assertEqual(report['corpus'], 'attachment')
        self.assertEqual(report['total_searches'], 1)
        self.assertEqual(report['total_misses'], 1)
        self.assertEqual(report['top_unanswered'][0]['query'], 'Attachment miss')

    def test_ledger_failure_never_fails_the_search(self):
        with mock.patch.object(
            RetrievalMiss.objects, 'create', side_effect=RuntimeError('db down')
        ):
            result = self._search()
        self.assertEqual(result['returned_count'], 0)
        self.assertEqual(RetrievalMiss.objects.count(), 0)

    def test_rollup_command_reports_top_unanswered(self):
        for _ in range(3):
            self._search(query='What is the seal replacement interval?')
        self._search(query='Where is the isolation valve?')
        self._search(
            rows=[{'chunk': 'x', '@search.score': 1.0, 'document_id': 'd'}],
            query='What torque for the coupling?',
        )
        out = StringIO()
        call_command('retrieval_misses', '--json', stdout=out)
        import json

        report = json.loads(out.getvalue())
        self.assertEqual(report['total_searches'], 5)
        self.assertEqual(report['total_misses'], 4)
        self.assertEqual(
            report['top_unanswered'][0]['query'],
            'What is the seal replacement interval?',
        )
        self.assertEqual(report['top_unanswered'][0]['asked'], 3)

    def test_weak_report_surfaces_low_score_hits(self):
        """P8-W0b: weak-but-nonzero hits are the over-caution suspects."""
        self._search(query='Zero hit question')
        self._search(
            rows=[{'chunk': 'x', '@search.score': 0.31, 'document_id': 'd'}],
            query='What torque for the coupling?',
        )
        self._search(
            rows=[{'chunk': 'x', '@search.score': 2.4, 'document_id': 'd'}],
            query='Strong hit question',
        )
        out = StringIO()
        call_command('retrieval_misses', '--json', '--weak', '0.5', stdout=out)
        import json

        report = json.loads(out.getvalue())
        self.assertEqual(report['weak_threshold'], 0.5)
        self.assertEqual(report['total_weak'], 1)
        self.assertEqual(
            report['top_weak'][0]['query'], 'What torque for the coupling?'
        )
        # The strong hit and the zero-hit rows never appear in the weak list.
        weak_queries = {row['query'] for row in report['top_weak']}
        self.assertNotIn('Strong hit question', weak_queries)
        self.assertNotIn('Zero hit question', weak_queries)


@tag('migration_test')
class RetrievalMissMigrationTests(TransactionTestCase):
    """Forward and reverse paths for the additive S16/S17 migrations."""

    def test_embedding_stamp_and_ledger_round_trip(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate([('aichat', '0011_messagefeedback')])
        tables = set(connection.introspection.table_names())
        self.assertNotIn('aichat_retrievalmiss', tables)

        MigrationExecutor(connection).migrate([('aichat', '0013_retrievalmiss')])
        tables = set(connection.introspection.table_names())
        self.assertIn('aichat_retrievalmiss', tables)
        columns = {
            column.name
            for column in connection.introspection.get_table_description(
                connection.cursor().cursor, 'aichat_controlleddocument'
            )
        }
        self.assertIn('embedding_model', columns)
        self.assertIn('embedding_dimensions', columns)

        MigrationExecutor(connection).migrate([('aichat', '0011_messagefeedback')])
        self.assertNotIn(
            'aichat_retrievalmiss', set(connection.introspection.table_names())
        )
        MigrationExecutor(connection).migrate([('aichat', '0013_retrievalmiss')])
