"""Governed re-embed of current controlled documents (S17 A4).

Re-projects every current indexed revision with the *configured* embedding
model, re-reading each registered source from the mounted controlled root.
This is the only sanctioned path for changing the corpus's embedding model:
plain ingestion refuses a model mismatch
(``CONTROLLED_DOCUMENT_EMBEDDING_MODEL_DRIFT``).

A dimensionality change cannot be re-embedded in place — the index's vector
field is fixed — so that migration needs a new index plus a configuration
change, and this command will refuse it at the drift check.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ai.core.config import get_settings
from ai.core.integrations.controlled_document_indexing import (
    ControlledDocumentIndexer,
    ControlledDocumentIngestionError,
    ControlledDocumentMetadata,
)
from aichat.models import ControlledDocument, ControlledDocumentState


class Command(BaseCommand):
    """Re-embed current controlled documents with the configured model."""

    help = 'Re-embed current controlled documents with the configured embedding model'

    def add_arguments(self, parser) -> None:
        """Register the governed narrowing and acknowledgment options."""
        parser.add_argument(
            '--document-id', default='', help='Limit to one document id'
        )
        parser.add_argument(
            '--retire-index',
            action='store_true',
            help=(
                'Acknowledge that revisions embedded with a DIFFERENT model will '
                'have their vectors replaced in place. Required for a model '
                'migration; a same-model backfill does not need it.'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would re-embed, change nothing',
        )

    def handle(self, *args, **options) -> None:
        """Run the governed re-embed over every matching current revision."""
        settings = get_settings()
        configured_model = settings.azure_openai_embedding_deployment
        rows = ControlledDocument.objects.filter(
            state=ControlledDocumentState.INDEXED, is_current=True
        ).order_by('scope_key', 'document_id')
        if options['document_id']:
            rows = rows.filter(document_id=options['document_id'])
        rows = list(rows)
        if not rows:
            self.stdout.write('No current indexed revisions match.')
            return

        migrating = [
            row
            for row in rows
            if row.embedding_model and row.embedding_model != configured_model
        ]
        if migrating and not options['retire_index']:
            raise CommandError(
                f'{len(migrating)} revision(s) are stamped with a different embedding '
                f'model than the configured {configured_model!r}. Re-run with '
                '--retire-index to acknowledge replacing their vectors.'
            )

        root = settings.controlled_documents_root.resolve()
        report: list[dict[str, object]] = []
        indexer = None
        for row in rows:
            entry: dict[str, object] = {
                'document_id': row.document_id,
                'revision': row.revision,
                'stamped_model': row.embedding_model,
            }
            source = Path(row.source_location)
            try:
                source = source.expanduser().resolve()
                source.relative_to(root)
            except (OSError, ValueError):
                entry['outcome'] = 'skipped_source_outside_root'
                report.append(entry)
                continue
            if not source.is_file():
                entry['outcome'] = 'skipped_source_missing'
                report.append(entry)
                continue
            if options['dry_run']:
                entry['outcome'] = 'would_reembed'
                report.append(entry)
                continue
            if indexer is None:
                indexer = ControlledDocumentIndexer.from_settings(
                    allow_model_change=True
                )
            metadata = ControlledDocumentMetadata(
                document_id=row.document_id,
                revision=row.revision,
                title=row.title,
                document_class=row.document_class,
                scope_key=row.scope_key,
                access_class=row.access_class,
                source_location=row.source_location,
                revision_date=row.revision_date,
                facility=row.facility,
                process_area=row.process_area,
                asset_id=row.asset_id,
                child_asset_id=row.child_asset_id,
                work_order_id=row.work_order_id,
                repair_packet_id=row.repair_packet_id,
                created_by=row.created_by,
                approved_by=row.approved_by,
            )
            try:
                result = indexer.ingest(
                    source_path=source, metadata=metadata, force=True
                )
            except ControlledDocumentIngestionError as exc:
                entry['outcome'] = 'failed'
                entry['error_code'] = exc.code
                report.append(entry)
                continue
            entry['outcome'] = 'reembedded'
            entry['chunk_count'] = result.manifest.get('chunk_count')
            entry['embedding_model'] = configured_model
            report.append(entry)

        self.stdout.write(
            json.dumps({'configured_model': configured_model, 'rows': report})
        )
        if any(item.get('outcome') == 'failed' for item in report):
            raise CommandError(
                'One or more revisions failed to re-embed; see report above.'
            )
