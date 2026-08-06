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
        self.assertEqual(result['total'], 0)
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
                'created_at',
            },
        )

    def test_hit_search_records_count_and_top_score(self):
        rows = [
            {'chunk': 'Torque to 45 Nm', '@search.score': 2.5, 'document_id': 'd'},
            {'chunk': 'Use thread locker', '@search.score': 1.5, 'document_id': 'd'},
        ]
        result = self._search(rows=rows)
        self.assertEqual(result['total'], 2)
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

    def test_ledger_failure_never_fails_the_search(self):
        with mock.patch.object(
            RetrievalMiss.objects, 'create', side_effect=RuntimeError('db down')
        ):
            result = self._search()
        self.assertEqual(result['total'], 0)
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
