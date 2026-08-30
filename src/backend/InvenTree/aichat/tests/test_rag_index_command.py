"""create_rag_search_indexes: schemas as code, alias refusal, dry-run (R0-5).

The two index builders ARE the deployed §5.1/§5.2 schemas — this suite pins
them field-by-field so a drive-by edit cannot silently drift the serving
contract, and exercises the refusal/dry-run branches the review found had
never run in CI.
"""

from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from aichat.management.commands.create_rag_search_indexes import (
    build_attachment_docs_index,
    build_media_evidence_index,
)

_DOCS_FIELDS = {
    'id',
    'attachment_id',
    'source_sha256',
    'model_type',
    'model_id',
    'part_id',
    'part_name',
    'asset_id',
    'machine_name',
    'client_codes',
    'scope_key',
    'access_class',
    'is_current',
    'doc_type',
    'source_file_name',
    'section_path',
    'heading_1',
    'heading_2',
    'heading_3',
    'page_number',
    'chunk_index',
    'token_count',
    'content',
    'text_vector',
    'language',
    'uploaded_at',
    'indexed_at',
    'as_of',
    'embedding_model',
    'embedding_dimensions',
    'embedding_profile',  # R5: shared _stamp_fields, both spaces
}

_MEDIA_FIELDS = {
    'id',
    'attachment_id',
    'source_sha256',
    'media_type',
    'model_type',
    'model_id',
    'work_order_id',
    'step_execution_id',
    'asset_id',
    'machine_name',
    'client_codes',
    'scope_key',
    'access_class',
    'is_current',
    'timecode_start_s',
    'timecode_end_s',
    'duration_s',
    'segment_index',
    'segment_count',
    'caption',
    'ocr_text',
    'transcript',
    'thumbnail_path',
    'source_file_name',
    'recorded_at',
    'uploaded_at',
    'indexed_at',
    'media_vector',
    'embedding_model',
    'embedding_dimensions',
    'embedding_profile',  # R5: shared _stamp_fields, both spaces
}


def _stub_settings(**overrides):
    """Command-facing settings stub (the pydantic guard is tested elsewhere)."""
    values = {
        'azure_search_endpoint': '',
        'azure_search_api_key': '',
        'azure_search_attachment_docs_index': 'aimms-attachment-docs-v1',
        'azure_search_media_index': 'aimms-media-evidence-v1',
        'azure_search_controlled_documents_index': 'eaits-manuals-v4a',
        'azure_search_documents_index': '',
        'cohere_embed_dimensions': 1536,
        'gemini_embed_dimensions': 3072,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class IndexSchemaTests(SimpleTestCase):
    """The builders match the spec §5.1/§5.2 field tables exactly."""

    def test_docs_index_fields_match_spec(self):
        """Text-space schema: field set, key, vector width, scope coordinate."""
        index = build_attachment_docs_index('aimms-attachment-docs-v1', 1536)
        by_name = {field.name: field for field in index.fields}
        self.assertEqual(set(by_name), _DOCS_FIELDS)
        self.assertTrue(by_name['id'].key)
        self.assertEqual(by_name['text_vector'].vector_search_dimensions, 1536)
        self.assertTrue(by_name['client_codes'].filterable)
        self.assertIn('Collection', str(by_name['client_codes'].type))
        for name in ('scope_key', 'access_class', 'is_current', 'attachment_id'):
            self.assertTrue(by_name[name].filterable, name)
        config = index.semantic_search.configurations[0]
        self.assertEqual(config.name, 'semantic-default')

    def test_media_index_fields_match_spec(self):
        """Media-space schema: field set, vector width, hybrid text legs."""
        index = build_media_evidence_index('aimms-media-evidence-v1', 3072)
        by_name = {field.name: field for field in index.fields}
        self.assertEqual(set(by_name), _MEDIA_FIELDS)
        self.assertEqual(by_name['media_vector'].vector_search_dimensions, 3072)
        for name in ('caption', 'ocr_text', 'transcript'):
            self.assertTrue(by_name[name].searchable, name)
        # Retrievable-only: the thumbnail path must never be a filter/facet.
        self.assertFalse(by_name['thumbnail_path'].filterable)

    def test_vector_profile_matches_governed_index(self):
        """HNSW m=4/efC=400/efS=500 cosine on both spaces."""
        for index in (
            build_attachment_docs_index('a', 8),
            build_media_evidence_index('b', 8),
        ):
            algo = index.vector_search.algorithms[0]
            self.assertEqual(algo.parameters.m, 4)
            self.assertEqual(algo.parameters.ef_construction, 400)
            self.assertEqual(algo.parameters.ef_search, 500)


class CommandBehaviorTests(SimpleTestCase):
    """Alias refusal, empty-name refusal, and network-free dry-run."""

    def test_refuses_alias_of_governed_index(self):
        """An exact governed-index alias refuses before any client exists."""
        with mock.patch(
            'ai.core.config.get_settings',
            return_value=_stub_settings(
                azure_search_attachment_docs_index='eaits-manuals-v4a'
            ),
        ):
            with self.assertRaisesMessage(CommandError, 'aliases a governed'):
                call_command('create_rag_search_indexes', '--dry-run')

    def test_refuses_padded_alias_of_governed_index(self):
        """F-01 defense-in-depth: compare stripped names at the command too."""
        with mock.patch(
            'ai.core.config.get_settings',
            return_value=_stub_settings(
                azure_search_controlled_documents_index=' eaits-manuals-v4a',
                azure_search_attachment_docs_index='eaits-manuals-v4a',
            ),
        ):
            with self.assertRaisesMessage(CommandError, 'aliases a governed'):
                call_command('create_rag_search_indexes', '--dry-run')

    def test_refuses_empty_index_name(self):
        """A blank configured name is refused loudly."""
        with mock.patch(
            'ai.core.config.get_settings',
            return_value=_stub_settings(azure_search_attachment_docs_index=''),
        ):
            with self.assertRaisesMessage(CommandError, 'name is empty'):
                call_command(
                    'create_rag_search_indexes', '--space', 'text', '--dry-run'
                )

    def test_dry_run_prints_both_schemas_without_a_client(self):
        """Dry-run needs no endpoint and never builds a SearchIndexClient."""
        out = StringIO()
        from aichat.management.commands.create_rag_search_indexes import Command

        with (
            mock.patch(
                'ai.core.config.get_settings', return_value=_stub_settings()
            ),
            mock.patch.object(
                Command,
                '_index_client',
                side_effect=AssertionError('dry-run must not touch Azure'),
            ),
        ):
            call_command('create_rag_search_indexes', '--dry-run', stdout=out)
        report = out.getvalue()
        self.assertIn('aimms-attachment-docs-v1', report)
        self.assertIn('aimms-media-evidence-v1', report)
        self.assertIn('1536', report)
        self.assertIn('3072', report)

    def test_apply_requires_endpoint(self):
        """A live apply without a Search endpoint refuses."""
        with mock.patch(
            'ai.core.config.get_settings', return_value=_stub_settings()
        ):
            with self.assertRaisesMessage(CommandError, 'AZURE_SEARCH_ENDPOINT'):
                call_command('create_rag_search_indexes')
