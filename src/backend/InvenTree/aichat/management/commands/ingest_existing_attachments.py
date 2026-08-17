"""Backfill existing part/machine attachments through the R1 doc pipeline.

Docs only (decision #8): media backfills with its own phase (R3/R4). Runs the
same idempotent ``run_ingest`` the receiver path uses, so re-runs short-circuit
on already-indexed (attachment, sha) pairs. ``--allow-pypdf`` is the explicit
extraction override (decision #12) — never a silent fallback.
"""

from django.core.management.base import BaseCommand, CommandError

#: Doc-shaped extensions worth walking. XLSX is included deliberately so the
#: decision-#11 exclusion is *recorded* on registry rows, not silently dropped.
_BACKFILL_EXTENSIONS = ('.pdf', '.docx', '.md', '.markdown', '.txt', '.xlsx')


class Command(BaseCommand):
    """Walk existing attachments through the attachment-RAG doc pipeline."""

    help = (
        'Backfill existing part/assetmachine document attachments into the '
        'attachment-RAG corpus (docs only; media backfills with R3/R4).'
    )

    def add_arguments(self, parser):
        """Register selection, preview, and extraction-override options."""
        parser.add_argument(
            '--model-type',
            nargs='+',
            default=['part', 'assetmachine'],
            choices=['part', 'assetmachine'],
            help='Owning model types to walk (default: part assetmachine)',
        )
        parser.add_argument(
            '--since',
            default='',
            help='Only attachments uploaded on/after this date (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report routing decisions without ingesting anything',
        )
        parser.add_argument(
            '--allow-pypdf',
            action='store_true',
            help=(
                'Explicit override (decision #12): fall back to pypdf when '
                'Document Intelligence fails; stamps extractor=pypdf_override'
            ),
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Stop after processing this many candidates (0 = no limit)',
        )
        parser.add_argument(
            '--sleep',
            type=float,
            default=0.0,
            help='Seconds to pause between live ingests (provider throttling)',
        )

    def handle(self, *args, **options):
        """Route (and optionally ingest) every matching attachment."""
        from django.conf import settings as django_settings

        from aichat.services.attachment_ingestion import (
            AttachmentIngestionError,
            route_attachment,
            run_ingest,
            structural_skip_reason,
        )
        from common.models import Attachment

        dry_run = options['dry_run']
        if not dry_run and not getattr(
            django_settings, 'AIMMS_ATTACHMENT_RAG_ENABLED', False
        ):
            raise CommandError(
                'AIMMS_ATTACHMENT_RAG_ENABLED is off; enable the Django-plane '
                'flag or use --dry-run'
            )

        rows = Attachment.objects.filter(model_type__in=options['model_type']).order_by(
            'pk'
        )
        if options['since']:
            from datetime import date

            try:
                since = date.fromisoformat(options['since'])
            except ValueError as exc:
                raise CommandError('--since must be YYYY-MM-DD') from exc
            rows = rows.filter(upload_date__gte=since)

        counts = {'ingested': 0, 'skipped': 0, 'failed': 0, 'filtered': 0}
        # One embedding client + one projection for the whole run (F-19):
        # built lazily on the first live ingest, closed in the finally.
        shared_embedder = None
        shared_projection = None
        processed = 0
        try:
            for attachment in rows.iterator():
                if options['limit'] and processed >= options['limit']:
                    break
                name = (
                    (attachment.attachment.name or '') if attachment.attachment else ''
                )
                structural = structural_skip_reason(attachment)
                if structural is not None or not name.lower().endswith(
                    _BACKFILL_EXTENSIONS
                ):
                    counts['filtered'] += 1
                    continue
                processed += 1
                if dry_run:
                    head = b''
                    try:
                        from django.core.files.storage import default_storage

                        with default_storage.open(name) as handle:
                            head = handle.read(1024)
                    except Exception:
                        self.stdout.write(f'{attachment.pk}\t{name}\tUNREADABLE')
                        counts['failed'] += 1
                        continue
                    decision = route_attachment(attachment, head)
                    label = decision.reason if decision.action == 'skip' else 'INGEST'
                    self.stdout.write(f'{attachment.pk}\t{name}\t{label}')
                    counts[
                        'ingested' if decision.action == 'ingest' else 'skipped'
                    ] += 1
                    continue
                if shared_embedder is None:
                    from ai.core.integrations.attachment_search import (
                        AttachmentSearchProjection,
                    )
                    from ai.core.integrations.embeddings_cohere import (
                        CohereEmbeddingClient,
                    )

                    shared_embedder = CohereEmbeddingClient.from_settings()
                    shared_projection = AttachmentSearchProjection.from_settings()
                try:
                    row = run_ingest(
                        attachment.pk,
                        allow_pypdf=options['allow_pypdf'],
                        embedding_client=shared_embedder,
                        projection=shared_projection,
                    )
                except AttachmentIngestionError as exc:
                    counts['failed'] += 1
                    self.stderr.write(f'{attachment.pk}\t{name}\tFAILED {exc.code}')
                    continue
                finally:
                    if options['sleep'] > 0:
                        import time

                        time.sleep(options['sleep'])
                if row is None:
                    counts['filtered'] += 1
                    continue
                if row.state == 'indexed':
                    counts['ingested'] += 1
                    self.stdout.write(
                        f'{attachment.pk}\t{name}\tINDEXED chunks={row.chunk_count}'
                    )
                elif row.state == 'skipped':
                    counts['skipped'] += 1
                    self.stdout.write(f'{attachment.pk}\t{name}\t{row.error_code}')
                else:
                    counts['failed'] += 1
                    self.stderr.write(
                        f'{attachment.pk}\t{name}\t{row.state.upper()} {row.error_code}'
                    )
        finally:
            for client in (shared_embedder, shared_projection):
                closer = getattr(client, 'close', None)
                if callable(closer):
                    closer()

        self.stdout.write(
            'done: ingested={ingested} skipped={skipped} failed={failed} '
            'filtered={filtered}'.format(**counts)
        )
        if counts['failed']:
            raise CommandError('Some attachments failed to ingest (see stderr)')
