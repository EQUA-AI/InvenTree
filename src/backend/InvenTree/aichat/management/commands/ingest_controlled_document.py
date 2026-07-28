"""Ingest a trusted mounted Markdown source into the controlled Search index."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ai.core.config import get_settings
from ai.core.integrations.controlled_document_indexing import (
    ControlledDocumentIndexer,
    ControlledDocumentIngestionError,
    ControlledDocumentMetadata,
)


class Command(BaseCommand):
    """Ingest one Azure Files controlled Markdown source through AIMMS safeguards."""

    help = 'Ingest a trusted controlled Markdown document from the mounted Azure Files root'

    def add_arguments(self, parser) -> None:
        """Register only trusted server-side source and metadata coordinates."""
        parser.add_argument(
            '--source', required=True, help='Mounted controlled Markdown path'
        )
        parser.add_argument('--document-id', required=True)
        parser.add_argument('--revision', required=True)
        parser.add_argument('--title', required=True)
        parser.add_argument('--document-class', required=True)
        parser.add_argument('--revision-date', required=True, help='YYYY-MM-DD')
        parser.add_argument('--scope-key', required=True)
        parser.add_argument('--access-class', required=True)
        parser.add_argument('--facility', default='')
        parser.add_argument('--process-area', default='')
        parser.add_argument('--asset-id', default='')
        parser.add_argument('--child-asset-id', default='')
        parser.add_argument('--work-order-id', default='')
        parser.add_argument('--repair-packet-id', default='')

    @staticmethod
    def _revision_date(value: str) -> date:
        """Parse the revision date without accepting ambiguous local formats."""
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError(
                '--revision-date must be an ISO date (YYYY-MM-DD)'
            ) from exc

    @staticmethod
    def _trusted_source_path(raw_path: str) -> Path:
        """Ensure the source is a real file below the mounted controlled-source root."""
        settings = get_settings()
        root = settings.controlled_documents_root.resolve()
        source = Path(raw_path).expanduser().resolve()
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise CommandError(
                f'--source must be beneath the controlled source root {root}'
            ) from exc
        if not source.is_file():
            raise CommandError('--source must identify an existing regular file')
        return source

    def handle(self, *args, **options) -> None:
        """Run one governed Azure Files-to-Search ingestion and print safe metadata."""
        source = self._trusted_source_path(options['source'])
        metadata = ControlledDocumentMetadata(
            document_id=options['document_id'],
            revision=options['revision'],
            title=options['title'],
            document_class=options['document_class'],
            scope_key=options['scope_key'],
            access_class=options['access_class'],
            source_location=str(source),
            revision_date=self._revision_date(options['revision_date']),
            facility=options['facility'],
            process_area=options['process_area'],
            asset_id=options['asset_id'],
            child_asset_id=options['child_asset_id'],
            work_order_id=options['work_order_id'],
            repair_packet_id=options['repair_packet_id'],
        )
        try:
            result = ControlledDocumentIndexer.from_settings().ingest(
                source_path=source, metadata=metadata
            )
        except ControlledDocumentIngestionError as exc:
            raise CommandError(exc.code) from exc

        report = {
            'document_id': result.document_id,
            'revision': result.revision,
            'source_sha256': result.source_sha256,
            'search_index_name': result.search_index_name,
            'section_count': result.manifest['section_count'],
            'chunk_count': result.manifest['chunk_count'],
            'already_indexed': result.already_indexed,
        }
        self.stdout.write(self.style.SUCCESS(json.dumps(report, sort_keys=True)))
