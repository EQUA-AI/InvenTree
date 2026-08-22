"""Backfill existing attachments through the attachment-RAG pipelines.

Docs landed in R1; image media (workorder/workorderstepexecution/assetmachine
owners) in R3; video backfills with R4. Runs the same idempotent
``run_ingest`` the receiver path uses, so re-runs short-circuit on
already-indexed (attachment, sha) pairs. ``--allow-pypdf`` is the explicit
extraction override (decision #12) — never a silent fallback.
"""

from django.core.management.base import BaseCommand, CommandError

#: Extensions worth walking. XLSX is included deliberately so the decision-#11
#: exclusion is *recorded* on registry rows, not silently dropped; likewise
#: the non-embeddable raster/video formats record their skips (R3).
_BACKFILL_EXTENSIONS = (
    '.pdf',
    '.docx',
    '.md',
    '.markdown',
    '.txt',
    '.xlsx',
    '.png',
    '.jpg',
    '.jpeg',
    '.webp',
    '.gif',
    '.bmp',
    '.tif',
    '.tiff',
    '.mp4',
    '.mov',
    '.m4v',
    '.avi',
    '.mkv',
    '.webm',
)


class Command(BaseCommand):
    """Walk existing attachments through the attachment-RAG doc pipeline."""

    help = (
        'Backfill existing attachments into the attachment-RAG corpora '
        '(docs since R1, images since R3; video backfills with R4).'
    )

    def add_arguments(self, parser):
        """Register selection, preview, and extraction-override options."""
        parser.add_argument(
            '--model-type',
            nargs='+',
            default=['part', 'assetmachine'],
            choices=['part', 'assetmachine', 'workorder', 'workorderstepexecution'],
            help=(
                'Owning model types to walk (default: part assetmachine; '
                'media owners are opt-in)'
            ),
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
            media_ingest_enabled,
            route_attachment,
            run_ingest,
            structural_skip_reason,
        )
        from common.models import Attachment

        ai_settings = None
        try:
            from ai.core.config import get_settings

            ai_settings = get_settings()
        except Exception:
            ai_settings = None

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
        # One client set for the whole run (F-19): the doc pair on the first
        # live candidate, the media pair on the first image candidate; all
        # closed in the finally.
        shared_embedder = None
        shared_projection = None
        shared_media_embedder = None
        shared_media_projection = None
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
                is_media_candidate = name.lower().endswith((
                    '.png',
                    '.jpg',
                    '.jpeg',
                    '.webp',
                    '.mp4',
                    '.mov',
                    '.m4v',
                ))
                # avi/mkv/webm are walked so their exclusions get RECORDED,
                # but they can never reach the media pipeline (R4 mp4/mov
                # allowlist) — build no clients for them in either direction.
                is_video_skip_candidate = name.lower().endswith((
                    '.avi',
                    '.mkv',
                    '.webm',
                ))
                if (
                    shared_embedder is None
                    and not is_media_candidate
                    and not is_video_skip_candidate
                ):
                    # Doc pair only for doc-shaped candidates: a media-only
                    # backfill must not require (or crash building) the
                    # Cohere client it will never use (review finding, R3).
                    from ai.core.integrations.attachment_search import (
                        AttachmentSearchProjection,
                    )
                    from ai.core.integrations.embeddings_cohere import (
                        CohereEmbeddingClient,
                    )

                    shared_embedder = CohereEmbeddingClient.from_settings()
                    shared_projection = AttachmentSearchProjection.from_settings()
                if (
                    shared_media_embedder is None
                    and is_media_candidate
                    and media_ingest_enabled(ai_settings)
                ):
                    from ai.core.integrations.attachment_search import (
                        MediaSearchProjection,
                    )
                    from ai.core.integrations.embeddings_gemini import (
                        GeminiEmbeddingClient,
                    )

                    shared_media_embedder = GeminiEmbeddingClient.from_settings()
                    shared_media_projection = MediaSearchProjection.from_settings()
                try:
                    row = run_ingest(
                        attachment.pk,
                        allow_pypdf=options['allow_pypdf'],
                        embedding_client=shared_embedder,
                        projection=shared_projection,
                        media_embedding_client=shared_media_embedder,
                        media_projection=shared_media_projection,
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
                    detail = (
                        f'segments={row.segment_count}'
                        if row.pipeline in ('image', 'video')
                        else f'chunks={row.chunk_count}'
                    )
                    self.stdout.write(f'{attachment.pk}\t{name}\tINDEXED {detail}')
                elif row.state == 'skipped':
                    counts['skipped'] += 1
                    self.stdout.write(f'{attachment.pk}\t{name}\t{row.error_code}')
                else:
                    counts['failed'] += 1
                    self.stderr.write(
                        f'{attachment.pk}\t{name}\t{row.state.upper()} {row.error_code}'
                    )
        finally:
            for client in (
                shared_embedder,
                shared_projection,
                shared_media_embedder,
                shared_media_projection,
            ):
                closer = getattr(client, 'close', None)
                if callable(closer):
                    closer()

        self.stdout.write(
            'done: ingested={ingested} skipped={skipped} failed={failed} '
            'filtered={filtered}'.format(**counts)
        )
        if counts['failed']:
            raise CommandError('Some attachments failed to ingest (see stderr)')
