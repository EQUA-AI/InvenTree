"""Create or update the two attachment-RAG Azure AI Search indexes (R0).

Index schemas live here as code so both spaces are reproducible:

- text space  (``aimms-attachment-docs-v1``):  Cohere Embed v4 vectors
- media space (``aimms-media-evidence-v1``):   Gemini Embedding 2 vectors

Never touches the governed controlled-document index; aliasing it is refused.
"""

import json

from django.core.management.base import BaseCommand, CommandError

VECTOR_PROFILE = 'vector-default'
HNSW_NAME = 'hnsw-default'
SEMANTIC_CONFIG = 'semantic-default'


def _index_models():
    """Import the Search index model surface lazily (deployment packaging)."""
    try:
        from azure.search.documents.indexes import SearchIndexClient
        from azure.search.documents.indexes import models as m
    except ImportError as exc:  # pragma: no cover - deployment packaging
        raise CommandError('azure-search-documents SDK is unavailable') from exc
    return SearchIndexClient, m


def _vector_search(m, *, profile: str = VECTOR_PROFILE):
    """HNSW cosine profile matching the governed index (m4/efC400/efS500)."""
    return m.VectorSearch(
        algorithms=[
            m.HnswAlgorithmConfiguration(
                name=HNSW_NAME,
                parameters=m.HnswParameters(
                    m=4,
                    ef_construction=400,
                    ef_search=500,
                    metric=m.VectorSearchAlgorithmMetric.COSINE,
                ),
            )
        ],
        profiles=[
            m.VectorSearchProfile(name=profile, algorithm_configuration_name=HNSW_NAME)
        ],
    )


def _stamp_fields(m):
    """S17 embedding stamp fields carried by every RAG index."""
    return [
        m.SimpleField(name='embedding_model', type='Edm.String', filterable=True),
        m.SimpleField(name='embedding_dimensions', type='Edm.Int32', filterable=True),
    ]


def build_attachment_docs_index(name: str, dimensions: int):
    """Text-space index over auto-ingested part/machine documents (spec 5.1)."""
    _, m = _index_models()
    fields = [
        m.SimpleField(name='id', type='Edm.String', key=True),
        m.SimpleField(name='attachment_id', type='Edm.Int64', filterable=True),
        m.SimpleField(name='source_sha256', type='Edm.String', filterable=True),
        m.SimpleField(
            name='model_type', type='Edm.String', filterable=True, facetable=True
        ),
        m.SimpleField(name='model_id', type='Edm.Int64', filterable=True),
        m.SimpleField(name='part_id', type='Edm.Int64', filterable=True),
        m.SearchableField(name='part_name', type='Edm.String'),
        m.SimpleField(name='asset_id', type='Edm.String', filterable=True),
        m.SearchableField(name='machine_name', type='Edm.String'),
        m.SearchField(
            name='client_codes',
            type='Collection(Edm.String)',
            filterable=True,
            searchable=False,
        ),
        m.SimpleField(name='scope_key', type='Edm.String', filterable=True),
        m.SimpleField(name='access_class', type='Edm.String', filterable=True),
        m.SimpleField(name='is_current', type='Edm.Boolean', filterable=True),
        m.SimpleField(
            name='doc_type', type='Edm.String', filterable=True, facetable=True
        ),
        m.SearchableField(name='source_file_name', type='Edm.String', filterable=True),
        m.SearchableField(name='section_path', type='Edm.String'),
        m.SearchableField(name='heading_1', type='Edm.String'),
        m.SearchableField(name='heading_2', type='Edm.String'),
        m.SearchableField(name='heading_3', type='Edm.String'),
        m.SimpleField(
            name='page_number', type='Edm.Int32', filterable=True, sortable=True
        ),
        m.SimpleField(name='chunk_index', type='Edm.Int32', sortable=True),
        m.SimpleField(name='token_count', type='Edm.Int32'),
        m.SearchableField(
            name='content', type='Edm.String', analyzer_name='en.microsoft'
        ),
        m.SearchField(
            name='text_vector',
            type='Collection(Edm.Single)',
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name=VECTOR_PROFILE,
        ),
        m.SimpleField(name='language', type='Edm.String', filterable=True),
        m.SimpleField(
            name='uploaded_at',
            type='Edm.DateTimeOffset',
            filterable=True,
            sortable=True,
        ),
        m.SimpleField(
            name='indexed_at', type='Edm.DateTimeOffset', filterable=True, sortable=True
        ),
        m.SimpleField(
            name='as_of', type='Edm.DateTimeOffset', filterable=True, sortable=True
        ),
        *_stamp_fields(m),
    ]
    semantic = m.SemanticSearch(
        configurations=[
            m.SemanticConfiguration(
                name=SEMANTIC_CONFIG,
                prioritized_fields=m.SemanticPrioritizedFields(
                    title_field=m.SemanticField(field_name='source_file_name'),
                    content_fields=[m.SemanticField(field_name='content')],
                    keywords_fields=[m.SemanticField(field_name='section_path')],
                ),
            )
        ]
    )
    return m.SearchIndex(
        name=name,
        fields=fields,
        vector_search=_vector_search(m),
        semantic_search=semantic,
    )


def build_media_evidence_index(name: str, dimensions: int):
    """Media-space index over evidence images and video segments (spec 5.2)."""
    _, m = _index_models()
    fields = [
        m.SimpleField(name='id', type='Edm.String', key=True),
        m.SimpleField(name='attachment_id', type='Edm.Int64', filterable=True),
        m.SimpleField(name='source_sha256', type='Edm.String', filterable=True),
        m.SimpleField(
            name='media_type', type='Edm.String', filterable=True, facetable=True
        ),
        m.SimpleField(name='model_type', type='Edm.String', filterable=True),
        m.SimpleField(name='model_id', type='Edm.Int64', filterable=True),
        m.SimpleField(name='work_order_id', type='Edm.Int64', filterable=True),
        m.SimpleField(name='step_execution_id', type='Edm.Int64', filterable=True),
        m.SimpleField(name='asset_id', type='Edm.String', filterable=True),
        m.SearchableField(name='machine_name', type='Edm.String'),
        m.SearchField(
            name='client_codes',
            type='Collection(Edm.String)',
            filterable=True,
            searchable=False,
        ),
        m.SimpleField(name='scope_key', type='Edm.String', filterable=True),
        m.SimpleField(name='access_class', type='Edm.String', filterable=True),
        m.SimpleField(name='is_current', type='Edm.Boolean', filterable=True),
        m.SimpleField(
            name='timecode_start_s', type='Edm.Double', filterable=True, sortable=True
        ),
        m.SimpleField(
            name='timecode_end_s', type='Edm.Double', filterable=True, sortable=True
        ),
        m.SimpleField(name='duration_s', type='Edm.Double', filterable=True),
        m.SimpleField(name='segment_index', type='Edm.Int32', sortable=True),
        m.SimpleField(name='segment_count', type='Edm.Int32'),
        m.SearchableField(name='caption', type='Edm.String'),
        m.SearchableField(name='ocr_text', type='Edm.String'),
        m.SearchableField(name='transcript', type='Edm.String'),
        m.SimpleField(name='thumbnail_path', type='Edm.String'),
        m.SearchableField(name='source_file_name', type='Edm.String', filterable=True),
        m.SimpleField(
            name='recorded_at',
            type='Edm.DateTimeOffset',
            filterable=True,
            sortable=True,
        ),
        m.SimpleField(
            name='uploaded_at',
            type='Edm.DateTimeOffset',
            filterable=True,
            sortable=True,
        ),
        m.SimpleField(
            name='indexed_at', type='Edm.DateTimeOffset', filterable=True, sortable=True
        ),
        m.SearchField(
            name='media_vector',
            type='Collection(Edm.Single)',
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name=VECTOR_PROFILE,
        ),
        *_stamp_fields(m),
    ]
    semantic = m.SemanticSearch(
        configurations=[
            m.SemanticConfiguration(
                name=SEMANTIC_CONFIG,
                prioritized_fields=m.SemanticPrioritizedFields(
                    title_field=m.SemanticField(field_name='source_file_name'),
                    content_fields=[
                        m.SemanticField(field_name='caption'),
                        m.SemanticField(field_name='ocr_text'),
                        m.SemanticField(field_name='transcript'),
                    ],
                ),
            )
        ]
    )
    return m.SearchIndex(
        name=name,
        fields=fields,
        vector_search=_vector_search(m),
        semantic_search=semantic,
    )


class Command(BaseCommand):
    """Create/update the attachment-RAG Search indexes from their code schemas."""

    help = 'Create or update the attachment-RAG Azure AI Search indexes (R0).'

    def add_arguments(self, parser):
        """Register --space and --dry-run."""
        parser.add_argument(
            '--space',
            choices=['text', 'media', 'all'],
            default='all',
            help='Which embedding space to create (default: all).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print the index schemas without contacting the service.',
        )

    def handle(self, *args, **options):
        """Build the schemas, refuse aliasing, then apply unless --dry-run."""
        from ai.core.config import get_settings

        settings = get_settings()
        # Stripped comparison (F-01): whitespace must not defeat the guard,
        # even if the values arrive from somewhere other than the normalizing
        # Settings model.
        governed = {
            (settings.azure_search_controlled_documents_index or '').strip(),
            (settings.azure_search_documents_index or '').strip(),
        } - {''}
        targets = []
        if options['space'] in ('text', 'all'):
            targets.append(
                build_attachment_docs_index(
                    settings.azure_search_attachment_docs_index,
                    settings.cohere_embed_dimensions,
                )
            )
        if options['space'] in ('media', 'all'):
            targets.append(
                build_media_evidence_index(
                    settings.azure_search_media_index, settings.gemini_embed_dimensions
                )
            )
        for index in targets:
            if not (index.name or '').strip():
                raise CommandError('RAG index name is empty')
            if index.name.strip() in governed:
                raise CommandError(
                    f'{index.name} aliases a governed/legacy index; refusing'
                )

        if options['dry_run']:
            for index in targets:
                self.stdout.write(json.dumps(index.as_dict(), indent=2, default=str))
                self.stdout.write('')
            return

        if not settings.azure_search_endpoint:
            raise CommandError('AZURE_SEARCH_ENDPOINT is required to apply indexes')
        client = self._index_client(settings)
        for index in targets:
            client.create_or_update_index(index)
            self.stdout.write(
                self.style.SUCCESS(f'index {index.name}: created/updated')
            )

    @staticmethod
    def _index_client(settings):
        """Key-backed local client or managed-identity client (house posture)."""
        SearchIndexClient, _ = _index_models()
        if settings.azure_search_api_key:
            from azure.core.credentials import AzureKeyCredential

            credential = AzureKeyCredential(settings.azure_search_api_key)
        else:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        return SearchIndexClient(
            endpoint=settings.azure_search_endpoint, credential=credential
        )
