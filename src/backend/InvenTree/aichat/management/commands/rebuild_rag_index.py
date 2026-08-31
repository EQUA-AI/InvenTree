"""Rebuild the attachment-RAG Search indexes from Postgres alone (R5 WP-D).

Postgres is the system of record; Azure AI Search is a serving layer. This
command proves that by re-projecting every INDEXED winner row through the
SAME builders the live ingest uses (``build_search_documents`` /
``build_media_documents``), with zero provider calls: vectors come from the
pgvector columns, captions/OCR/transcripts from ``MediaSegment``, headings
and content from ``AttachmentChunk``, and every stamp (``embedding_model`` /
``dimensions`` / ``profile``, ``indexed_at``, ``media_recorded_at``) from the
row that made them — never from config, because the vectors were made by that
model under that profile.

``--space`` is required with no ``all``: the refusal sets differ per space
and a single failure must not half-finish both.

Known honest drift: ``doc_type``, owner coordinates, ``scope_key``,
``uploaded_at``, ``source_file_name`` and ``client_codes`` are re-derived
from live DB state rather than frozen, so ``--verify`` classifies diffs in
those fields separately from hard mismatches (ids, content, headings,
vectors, stamps).
"""

import json
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError

#: Fields legitimately re-derived from live DB state at projection time.
#: A --verify diff confined to these is drift, not corruption.
#: part_id / work_order_id / step_execution_id are NOT here: the coordinate
#: helpers derive them purely from the frozen row (model_type, model_id), so
#: a rebuild reproduces them exactly and a diff there is real corruption.
_REDERIVED_FIELDS = frozenset({
    'doc_type',
    'part_name',
    'asset_id',
    'machine_name',
    'scope_key',
    'uploaded_at',
    'source_file_name',
    'client_codes',
})

#: Edm.DateTimeOffset fields: Azure returns them UTC-normalized with a 'Z'
#: suffix while the builders emit Python isoformat ('+00:00'), so --verify
#: compares these as INSTANTS, never as strings.
_DATETIME_FIELDS = frozenset({'indexed_at', 'as_of', 'uploaded_at', 'recorded_at'})


def _same_instant(a, b) -> bool:
    from datetime import UTC, datetime

    def parse(value):
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    try:
        return parse(a) == parse(b)
    except (TypeError, ValueError):
        return False


class Command(BaseCommand):
    """Re-project one index space from Postgres; upsert-only, no providers."""

    help = (
        'Rebuild the attachment (text) or media Search index from Postgres. '
        'Zero provider calls; documents are re-assembled through the live '
        'builders with stored vectors and row stamps, then upserted.'
    )

    def add_arguments(self, parser):
        """Register space selection, preview/verify modes, and overrides."""
        parser.add_argument(
            '--space',
            required=True,
            choices=['text', 'media'],
            help='Which index space to rebuild (no "all": refusal sets differ)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Build and count documents, write nothing',
        )
        parser.add_argument(
            '--verify',
            action='store_true',
            help=(
                'Build documents, fetch the live ones, and report per-field '
                'diffs (re-derived fields classified separately); writes '
                'nothing'
            ),
        )
        parser.add_argument(
            '--attachment-id',
            type=int,
            default=0,
            help='Narrow to one attachment (0 = all)',
        )
        parser.add_argument(
            '--allow-model-drift',
            action='store_true',
            help=(
                "Proceed when a row's stamped embedding_model differs from "
                "the configured one; the ROW's model is what gets projected"
            ),
        )
        parser.add_argument(
            '--allow-live',
            action='store_true',
            help=(
                'Proceed while ingests are mid-flight (EXTRACTING/EMBEDDING '
                'rows exist); otherwise the command refuses'
            ),
        )

    # ------------------------------------------------------------------ #

    def _winner_ids(self, rows, model_cls, states):
        """Pk set of rows that are the current winner for their attachment.

        Mirrors ``run_ingest``: the winner is ``max(_claim_order)`` over the
        attachment's non-terminal rows across EVERY pipeline — a content
        revert re-claims an OLD row, so ``-created_at`` would invert it.
        """
        from aichat.services.attachment_ingestion import _claim_order

        winners = set()
        for row in rows:
            peers = list(
                model_cls.objects.filter(attachment_id=row.attachment_id).exclude(
                    state__in=states
                )
            )
            if peers and max(peers, key=_claim_order).pk == row.pk:
                winners.add(row.pk)
        return winners

    def _build_text(self, row, attachment, settings):
        """Documents for one text row, from stored chunks only."""
        from pathlib import PurePosixPath

        from aichat.services.attachment_ingestion import (
            _tag_names,
            build_search_documents,
            classify_doc_type,
        )

        chunks = list(row.chunks.order_by('chunk_index'))
        if not chunks:
            return None, 'no_chunks'
        if row.chunk_count and row.chunk_count != len(chunks):
            # A lost TAIL row keeps indices contiguous; only the stored count
            # can catch it.
            return None, 'chunk_count_mismatch'
        indices = [chunk.chunk_index for chunk in chunks]
        if indices != list(range(len(chunks))):
            # The document id derives from the enumerate position, so a gap
            # would silently re-key every later chunk.
            return None, 'chunk_gap'
        if any(chunk.embedding is None for chunk in chunks):
            return None, 'refused_null_vector'
        stand_ins = [
            SimpleNamespace(
                text=chunk.content,
                section_path=chunk.section_path,
                heading_1=chunk.heading_1,
                heading_2=chunk.heading_2,
                heading_3=chunk.heading_3,
                token_count=chunk.token_count,
                # section_id is not stored; key the page map synthetically so
                # page_number round-trips through the untouched builder.
                section_id=str(chunk.chunk_index),
            )
            for chunk in chunks
        ]
        section_pages = {
            str(chunk.chunk_index): chunk.page_number
            for chunk in chunks
            if chunk.page_number is not None
        }
        documents = build_search_documents(
            ingest=row,
            attachment=attachment,
            chunks=stand_ins,
            vectors=[list(chunk.embedding) for chunk in chunks],
            client_codes=list(row.client_codes or []),
            scope_key=settings.single_site_policy_key,
            doc_type=classify_doc_type(
                PurePosixPath(attachment.attachment.name or '').name,
                _tag_names(attachment),
            ),
            section_pages=section_pages,
            embedding_model=row.embedding_model,
            embedding_dimensions=row.embedding_dimensions,
            embedding_profile=row.embedding_profile,
            indexed_at=row.indexed_at,
        )
        return documents, None

    def _build_media(self, row, attachment, settings):
        """Documents for one media row, from stored segments only."""
        from aichat.services.attachment_ingestion import (
            MediaDocSegment,
            build_media_documents,
        )

        segments = list(row.segments.order_by('segment_index'))
        if not segments:
            return None, 'no_segments'
        if row.segment_count and row.segment_count != len(segments):
            return None, 'segment_count_mismatch'
        if any(segment.embedding is None for segment in segments):
            return None, 'refused_null_vector'
        doc_segments = [
            MediaDocSegment(
                media_type=segment.media_type,
                # PG stores 0 for images but the live doc key is '-img';
                # passing 0 would mint '-s0' and duplicate the live document.
                segment_index=(
                    None if segment.media_type == 'image' else segment.segment_index
                ),
                timecode_start_s=segment.timecode_start_s,
                timecode_end_s=segment.timecode_end_s,
                caption=segment.caption,
                ocr_text=segment.ocr_text,
                transcript=segment.transcript,
                vector=list(segment.embedding),
                thumbnail_path=segment.thumbnail_path,
            )
            for segment in segments
        ]
        documents = build_media_documents(
            ingest=row,
            attachment=attachment,
            segments=doc_segments,
            client_codes=list(row.client_codes or []),
            scope_key=settings.single_site_policy_key,
            recorded_at=row.media_recorded_at,
            embedding_model=row.embedding_model,
            embedding_dimensions=row.embedding_dimensions,
            embedding_profile=row.embedding_profile,
            indexed_at=row.indexed_at,
        )
        return documents, None

    def _verify_row(self, client, row, documents):
        """Fetch live docs for the row and diff field-for-field."""
        live_docs = {}
        rows = client.search(
            search_text='*',
            filter=(
                f'attachment_id eq {int(row.attachment_id)}'
                f" and source_sha256 eq '{row.source_sha256}'"
                ' and is_current eq true'
            ),
            top=1000,
        )
        for doc in rows:
            live_docs[str(doc.get('id'))] = {
                key: value
                for key, value in dict(doc).items()
                if not key.startswith('@')
            }
        hard, drift = [], []
        missing = []
        for built in documents:
            live = live_docs.pop(str(built['id']), None)
            if live is None:
                missing.append(built['id'])
                continue
            for key, value in built.items():
                live_value = live.get(key)
                if key in ('text_vector', 'media_vector'):
                    same = (
                        live_value is not None
                        and len(live_value) == len(value)
                        and all(
                            # Exact on purpose: PG float4 <-> Edm.Single is
                            # bit-identical, so equality IS the DR contract.
                            float(a) == float(b)  # noqa: RUF069
                            for a, b in zip(value, live_value, strict=True)
                        )
                    )
                elif key in _DATETIME_FIELDS and value and live_value:
                    same = _same_instant(value, live_value)
                else:
                    same = value == live_value
                if not same:
                    (drift if key in _REDERIVED_FIELDS else hard).append(
                        f'{built["id"]}:{key}'
                    )
        return {
            'hard_mismatches': hard,
            'rederived_drift': drift,
            'missing_live_docs': missing,
            'extra_live_docs': sorted(live_docs),
        }

    # ------------------------------------------------------------------ #

    def handle(self, *args, **options):
        """Select winners, rebuild their documents, upsert or verify."""
        from ai.core.config import get_settings
        from aichat.models import AttachmentIngest, AttachmentIngestState
        from aichat.services.attachment_ingestion import _claim_order
        from common.models import Attachment

        space = options['space']
        settings = get_settings()
        if not (settings.single_site_policy_key or '').strip():
            # run_ingest hard-refuses this exact misconfiguration; a rebuild
            # would instead OVERWRITE every document's scope_key with '' (a
            # Search upload is a full replace) and vanish the corpus from
            # every properly configured retrieval filter.
            raise CommandError(
                'AIMMS_SINGLE_SITE_POLICY_KEY is not configured; a rebuild '
                'from this environment would blank scope_key on every '
                'document'
            )

        in_flight = AttachmentIngest.objects.filter(
            state__in=[
                AttachmentIngestState.EXTRACTING,
                AttachmentIngestState.EMBEDDING,
            ]
        ).count()
        if in_flight and not options['allow_live']:
            raise CommandError(
                f'{in_flight} ingest(s) are mid-flight; quiesce the workers '
                'or pass --allow-live'
            )

        pipelines = ('doc',) if space == 'text' else ('image', 'video')
        configured_dimensions = (
            settings.cohere_embed_dimensions
            if space == 'text'
            else settings.gemini_embed_dimensions
        )
        configured_model = (
            settings.cohere_embed_model
            if space == 'text'
            else settings.gemini_embed_model
        )

        selected = AttachmentIngest.objects.filter(
            state=AttachmentIngestState.INDEXED, pipeline__in=pipelines
        ).order_by('pk')
        if options['attachment_id']:
            selected = selected.filter(attachment_id=options['attachment_id'])
        rows = list(selected)

        # Pre-flight refusals: dimension drift is a hard error (the index
        # schema cannot hold it); model drift needs the explicit override.
        drifted_dimensions = [
            row.pk for row in rows if row.embedding_dimensions != configured_dimensions
        ]
        if drifted_dimensions:
            raise CommandError(
                f'{len(drifted_dimensions)} row(s) carry embedding_dimensions '
                f'!= {configured_dimensions}; a rebuild cannot proceed'
            )
        drifted_models = [
            row.pk
            for row in rows
            if row.embedding_model and row.embedding_model != configured_model
        ]
        if drifted_models and not options['allow_model_drift']:
            raise CommandError(
                f'{len(drifted_models)} row(s) carry a different '
                'embedding_model than configured; pass --allow-model-drift '
                "to project them under the ROW's model"
            )

        terminal = [
            AttachmentIngestState.DELETED,
            AttachmentIngestState.SKIPPED,
            AttachmentIngestState.SUPERSEDED,
        ]
        winner_ids = self._winner_ids(rows, AttachmentIngest, terminal)

        projection = None
        report = []
        failures = 0
        totals = {'selected': len(rows), 'rebuilt_documents': 0}
        try:
            for row in rows:
                entry = {
                    'ingest_id': row.pk,
                    'attachment_id': row.attachment_id,
                    'pipeline': row.pipeline,
                }
                report.append(entry)
                if row.pk not in winner_ids:
                    entry['outcome'] = 'superseded_by_peer'
                    continue
                if row.indexed_at is None:
                    # The 0031 repair (reverse projection + --force-unstamped)
                    # must run first; a rebuild must not invent stamps.
                    entry['outcome'] = 'refused_unstamped'
                    failures += 1
                    continue
                attachment = Attachment.objects.filter(pk=row.attachment_id).first()
                if attachment is None or not attachment.attachment:
                    entry['outcome'] = 'attachment_missing'
                    failures += 1
                    continue
                builder = self._build_text if space == 'text' else self._build_media
                documents, refusal = builder(row, attachment, settings)
                if refusal:
                    entry['outcome'] = refusal
                    failures += 1
                    continue
                entry['documents'] = len(documents)
                totals['rebuilt_documents'] += len(documents)
                if options['dry_run']:
                    entry['outcome'] = 'would_rebuild'
                    continue
                if projection is None:
                    # from_settings() ONLY: the index-alias guard lives there.
                    if space == 'text':
                        from ai.core.integrations.attachment_search import (
                            AttachmentSearchProjection,
                        )

                        projection = AttachmentSearchProjection.from_settings()
                    else:
                        from ai.core.integrations.attachment_search import (
                            MediaSearchProjection,
                        )

                        projection = MediaSearchProjection.from_settings()
                if options['verify']:
                    entry['verify'] = self._verify_row(
                        projection.client(), row, documents
                    )
                    clean = (
                        not entry['verify']['hard_mismatches']
                        and not entry['verify']['missing_live_docs']
                        # Surplus live docs under the same (attachment, sha,
                        # is_current) are stale segments — an inconsistency,
                        # not equality.
                        and not entry['verify']['extra_live_docs']
                    )
                    entry['outcome'] = 'verified_equal' if clean else 'verified_diff'
                    if not clean:
                        failures += 1
                    continue
                try:
                    projection.upsert_documents(documents)
                except Exception:
                    entry['outcome'] = 'upsert_failed'
                    failures += 1
                    continue
                # Resurrection race belts, mirroring BOTH of run_ingest's:
                # (a) the attachment was deleted (purge tombstones every row
                # DELETED, so the peer check below would see an EMPTY set and
                # skip — and the orphan sweep never revisits DELETED
                # tombstones, making resurrected docs permanent); (b) this
                # row lost its winner status while we wrote. Either way,
                # clean up exactly the sha just written. Never proactively.
                row.refresh_from_db(fields=['state'])
                attachment_gone = not Attachment.objects.filter(
                    pk=row.attachment_id
                ).exists()
                if attachment_gone or row.state == AttachmentIngestState.DELETED:
                    projection.mark_sha_stale(
                        attachment_id=row.attachment_id, source_sha256=row.source_sha256
                    )
                    projection.purge_sha(
                        attachment_id=row.attachment_id, source_sha256=row.source_sha256
                    )
                    entry['outcome'] = 'purged_after_delete'
                    continue
                peers = list(
                    AttachmentIngest.objects.filter(
                        attachment_id=row.attachment_id
                    ).exclude(state__in=terminal)
                )
                if peers and max(peers, key=_claim_order).pk != row.pk:
                    projection.mark_sha_stale(
                        attachment_id=row.attachment_id, source_sha256=row.source_sha256
                    )
                    projection.purge_sha(
                        attachment_id=row.attachment_id, source_sha256=row.source_sha256
                    )
                    entry['outcome'] = 'raced_and_cleaned'
                    continue
                entry['outcome'] = 'rebuilt'
        finally:
            if projection is not None:
                projection.close()

        mode = (
            'dry_run'
            if options['dry_run']
            else ('verify' if options['verify'] else 'rebuild')
        )
        self.stdout.write(
            json.dumps(
                {'space': space, 'mode': mode, 'totals': totals, 'rows': report},
                sort_keys=True,
                default=str,
            )
        )
        if failures:
            raise CommandError('Some rows could not be rebuilt (see report)')
