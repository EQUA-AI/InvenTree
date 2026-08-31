"""Backfill existing attachments through the attachment-RAG pipelines.

Docs landed in R1; image media (workorder/workorderstepexecution/assetmachine
owners) in R3; video backfills with R4. R5 hardened the walk: the extension
allow-list and its media shadows are gone — every row gets ONE head read and
ONE ``route_attachment`` call, and the client family comes from the router's
own ``decision.pipeline``, so the command can never build (or leak) a client
the router will not use. Runs the same idempotent ``run_ingest`` the receiver
path uses, so re-runs short-circuit on already-indexed (attachment, sha)
pairs; the receiver's O(stat) metadata stamp is consulted first so a no-op
re-run does not re-download the corpus. ``--allow-pypdf`` is the explicit
extraction override (decision #12) — never a silent fallback.

Selectors:

- ``--force`` re-ingests every walked row (bypasses the INDEXED
  short-circuit, the attempt cap, and the stamp pre-filter).
- ``--force-unstamped`` selects ``state=INDEXED, indexed_at IS NULL`` — rows
  written before migration 0031's columns were wired (R5 WP-B), i.e. the
  convergent repair selector for the 0031 gap.
- ``--force-stale-profile`` selects rows whose stamped ``embedding_profile``
  differs from the profile currently configured for their own space.
- ``--census`` walks the WHOLE corpus (every owner, ignoring --model-type /
  --since / --limit), head-reads and routes in-scope rows, writes nothing,
  and emits the same JSON histogram shape as a live run so the two diff.
"""

import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Walk existing attachments through the attachment-RAG pipelines."""

    help = (
        'Backfill existing attachments into the attachment-RAG corpora '
        '(docs since R1, images since R3, video since R4; census/force '
        'selectors since R5).'
    )

    def add_arguments(self, parser):
        """Register selection, preview, force, and extraction options."""
        from aichat.services.attachment_ingestion import RECEIVER_MODEL_TYPES

        parser.add_argument(
            '--model-type',
            nargs='+',
            default=None,
            choices=[*RECEIVER_MODEL_TYPES, 'all'],
            help=(
                'Owning model types to walk (default: part assetmachine, '
                'except under a force selector, where the default widens to '
                "every receiver-covered type; 'all' expands to the same). "
                'Media owners are opt-in on a plain walk.'
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
            '--census',
            action='store_true',
            help=(
                'Whole-corpus audit: walk every owner (ignores --model-type, '
                '--since and --limit), route in-scope rows, write nothing, '
                'and emit the JSON histogram a live run also emits'
            ),
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help=(
                'Re-ingest every walked row: bypasses the INDEXED '
                'short-circuit, the attempt cap, and the stamp pre-filter'
            ),
        )
        parser.add_argument(
            '--force-unstamped',
            action='store_true',
            help=(
                'Force-select INDEXED rows with indexed_at IS NULL (written '
                'before the 0031 columns were wired); convergent — a second '
                'run selects nothing'
            ),
        )
        parser.add_argument(
            '--force-stale-profile',
            action='store_true',
            help=(
                'Force-select rows whose embedding_profile differs from the '
                'profile configured for their own space (doc vs image/video)'
            ),
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
            help=(
                'Stop after routing this many candidates (0 = no limit). '
                'R5: counts every routed row, not just extension-matched '
                'ones, and stamp-skipped rows do not consume the budget'
            ),
        )
        parser.add_argument(
            '--sleep',
            type=float,
            default=0.0,
            help='Seconds to pause between live ingests (provider throttling)',
        )

    def _selected_attachment_ids(self, options, ai_settings):
        """Attachment ids matched by the force selectors (None = no selector).

        Both selectors read the ``AttachmentIngest`` registry;
        ``attachment_id`` is a plain integer mirror of ``Attachment.pk``
        (no FK — the attachment may outlive the registry on either env), so
        the result feeds a ``pk__in`` filter on the walk queryset.
        """
        if not (options['force_unstamped'] or options['force_stale_profile']):
            return None

        from django.db.models import Q

        from aichat.models import AttachmentIngest, AttachmentIngestState

        selected: set[int] = set()
        if options['force_unstamped']:
            unstamped = AttachmentIngest.objects.filter(
                state=AttachmentIngestState.INDEXED, indexed_at__isnull=True
            ).values_list('attachment_id', flat=True)
            unstamped = set(unstamped)
            self.stdout.write(f'selector: force-unstamped selected={len(unstamped)}')
            selected |= unstamped
        if options['force_stale_profile']:
            if ai_settings is None:
                raise CommandError(
                    '--force-stale-profile needs the AI settings to resolve '
                    'the configured profiles, and they failed to load'
                )
            from ai.core.integrations.rag_profile import (
                media_embedding_profile,
                text_embedding_profile,
            )

            stale = AttachmentIngest.objects.filter(
                (
                    Q(pipeline='doc')
                    & ~Q(embedding_profile=text_embedding_profile(ai_settings))
                )
                | (
                    Q(pipeline__in=('image', 'video'))
                    & ~Q(embedding_profile=media_embedding_profile(ai_settings))
                ),
                state=AttachmentIngestState.INDEXED,
            ).values_list('attachment_id', flat=True)
            stale = set(stale)
            suffix = '' if stale else ' (no profile drift)'
            self.stdout.write(
                f'selector: force-stale-profile selected={len(stale)}{suffix}'
            )
            selected |= stale
        return selected

    def _corpus_aggregates(self):
        """DB-derived histogram legs, identical in every mode (diffable)."""
        from django.db.models import Count

        from aichat.models import AttachmentIngest
        from aichat.services.attachment_ingestion import RECEIVER_MODEL_TYPES
        from common.models import Attachment

        by_profile = {
            (row['embedding_profile'] or ''): row['n']
            for row in AttachmentIngest.objects
            .values('embedding_profile')
            .annotate(n=Count('id'))
            .order_by('embedding_profile')
        }
        out_of_scope = {
            row['model_type']: row['n']
            for row in Attachment.objects
            .exclude(model_type__in=RECEIVER_MODEL_TYPES)
            .values('model_type')
            .annotate(n=Count('id'))
            .order_by('model_type')
        }
        return by_profile, out_of_scope

    def handle(self, *args, **options):
        """Route (and optionally ingest) every matching attachment."""
        from django.conf import settings as django_settings

        from aichat.receivers import _stamp_matches
        from aichat.services.attachment_ingestion import (
            RECEIVER_MODEL_TYPES,
            AttachmentIngestionError,
            _read_attachment_head,
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

        census = options['census']
        dry_run = options['dry_run']
        forcing = (
            options['force']
            or options['force_unstamped']
            or options['force_stale_profile']
        )
        live = not census and not dry_run
        if live and not getattr(django_settings, 'AIMMS_ATTACHMENT_RAG_ENABLED', False):
            raise CommandError(
                'AIMMS_ATTACHMENT_RAG_ENABLED is off; enable the Django-plane '
                'flag or use --dry-run/--census'
            )
        if live:
            # R5 posture C: with default-on Django flags, a provider-degraded
            # deployment must refuse a live walk rather than stream, hash and
            # skip-stamp the whole corpus for pipelines that cannot run.
            from aichat.receivers import _any_ingest_effective

            if not _any_ingest_effective():
                raise CommandError(
                    'No ingest pipeline is effective (providers incomplete or '
                    'flags degraded); fix the AI-plane configuration or use '
                    '--dry-run/--census'
                )

        selector_active = options['force_unstamped'] or options['force_stale_profile']
        if options['model_type'] is None:
            # A force selector targets registry rows wherever they live —
            # media rows sit on workorder/workorderstepexecution owners, so
            # the docs-era default scope would silently drop exactly the rows
            # the selector picked (R5 review finding). Explicit --model-type
            # still narrows deliberately, with the drop reported below.
            model_types = (
                list(RECEIVER_MODEL_TYPES)
                if selector_active
                else ['part', 'assetmachine']
            )
        else:
            model_types = list(options['model_type'])
        if 'all' in model_types:
            model_types = list(RECEIVER_MODEL_TYPES)

        if census:
            # Whole corpus, every owner: the completeness proof needs the
            # rows the receiver never covers to be walked and counted too.
            rows = Attachment.objects.order_by('pk')
        else:
            rows = Attachment.objects.filter(model_type__in=model_types).order_by('pk')
            selected = self._selected_attachment_ids(options, ai_settings)
            if selected is not None:
                rows = rows.filter(pk__in=selected)
                dropped = (
                    Attachment.objects
                    .filter(pk__in=selected)
                    .exclude(model_type__in=model_types)
                    .count()
                )
                if dropped:
                    self.stdout.write(
                        f'selector: WARNING {dropped} selected row(s) fall '
                        'outside --model-type and will not be walked'
                    )
            if options['since']:
                from datetime import date

                try:
                    since = date.fromisoformat(options['since'])
                except ValueError as exc:
                    raise CommandError('--since must be YYYY-MM-DD') from exc
                rows = rows.filter(upload_date__gte=since)

        counts = {
            'walked': 0,
            'processed': 0,
            'ingested': 0,
            'skipped': 0,
            'failed': 0,
            'filtered': 0,
            'stamp_skipped': 0,
            'would_ingest': 0,
            'would_skip': 0,
        }
        by_error_code: dict[str, int] = {}
        by_pipeline: dict[str, int] = {}
        by_model_type: dict[str, int] = {}
        # One client set for the whole run (F-19): each family on its first
        # routed candidate, both closed in the finally. The family comes from
        # the router's decision, never from the file name, so a client the
        # router will not use is never constructed. run_ingest's per-row
        # fallback still fires for one rare case — the cross-pipeline peer
        # purge builds the OTHER family's projection when a prior revision
        # of the same attachment routed through a different pipeline.
        shared_embedder = None
        shared_projection = None
        shared_media_embedder = None
        shared_media_projection = None
        processed = 0
        try:
            for attachment in rows.iterator():
                if not census and options['limit'] and processed >= options['limit']:
                    break
                counts['walked'] += 1
                by_model_type[attachment.model_type] = (
                    by_model_type.get(attachment.model_type, 0) + 1
                )
                name = (
                    (attachment.attachment.name or '') if attachment.attachment else ''
                )
                if census and attachment.model_type not in RECEIVER_MODEL_TYPES:
                    counts['filtered'] += 1
                    continue
                structural = structural_skip_reason(attachment)
                if structural is not None:
                    counts['filtered'] += 1
                    continue
                stamp_state = ((attachment.metadata or {}).get('ai_ingest') or {}).get(
                    'state'
                )
                if (
                    live
                    and not forcing
                    and stamp_state == 'indexed'
                    and _stamp_matches(attachment)
                ):
                    # O(stat): the receiver stamp already covers this exact
                    # file revision, so skip the download+hash a run_ingest
                    # call would pay before its own INDEXED short-circuit.
                    # Indexed stamps ONLY: skipped stamps can be config-
                    # dependent (the oversize caps), and the backfill is the
                    # repair tool that must re-route them after a cap raise —
                    # the receiver alone treats them terminal on the request
                    # path (R5 review finding).
                    counts['stamp_skipped'] += 1
                    self.stdout.write(f'{attachment.pk}\t{name}\tSTAMPED')
                    continue
                try:
                    head = _read_attachment_head(attachment)
                except AttachmentIngestionError as exc:
                    self.stdout.write(f'{attachment.pk}\t{name}\tUNREADABLE')
                    counts['failed'] += 1
                    by_error_code[exc.code] = by_error_code.get(exc.code, 0) + 1
                    continue
                decision = route_attachment(attachment, head)
                processed += 1
                counts['processed'] += 1
                by_pipeline[decision.pipeline] = (
                    by_pipeline.get(decision.pipeline, 0) + 1
                )
                if census or dry_run:
                    label = decision.reason if decision.action == 'skip' else 'INGEST'
                    if not census:
                        self.stdout.write(f'{attachment.pk}\t{name}\t{label}')
                    if decision.action == 'ingest':
                        counts['would_ingest'] += 1
                    else:
                        counts['would_skip'] += 1
                        by_error_code[decision.reason] = (
                            by_error_code.get(decision.reason, 0) + 1
                        )
                    continue
                if decision.action == 'ingest':
                    if decision.pipeline in ('image', 'video'):
                        if shared_media_embedder is None and media_ingest_enabled(
                            ai_settings
                        ):
                            from ai.core.integrations.attachment_search import (
                                MediaSearchProjection,
                            )
                            from ai.core.integrations.embeddings_gemini import (
                                GeminiEmbeddingClient,
                            )

                            shared_media_embedder = (
                                GeminiEmbeddingClient.from_settings()
                            )
                            shared_media_projection = (
                                MediaSearchProjection.from_settings()
                            )
                    elif shared_embedder is None:
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
                        media_embedding_client=shared_media_embedder,
                        media_projection=shared_media_projection,
                        force=forcing,
                    )
                except AttachmentIngestionError as exc:
                    counts['failed'] += 1
                    by_error_code[exc.code] = by_error_code.get(exc.code, 0) + 1
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
                    if row.error_code:
                        by_error_code[row.error_code] = (
                            by_error_code.get(row.error_code, 0) + 1
                        )
                    self.stdout.write(f'{attachment.pk}\t{name}\t{row.error_code}')
                else:
                    counts['failed'] += 1
                    if row.error_code:
                        by_error_code[row.error_code] = (
                            by_error_code.get(row.error_code, 0) + 1
                        )
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

        profiles = {'text': None, 'media': None}
        if ai_settings is not None:
            try:
                from ai.core.integrations.rag_profile import (
                    media_embedding_profile,
                    text_embedding_profile,
                )

                profiles = {
                    'text': text_embedding_profile(ai_settings),
                    'media': media_embedding_profile(ai_settings),
                }
            except Exception:
                pass
        by_profile, out_of_scope = self._corpus_aggregates()
        report = {
            'mode': 'census' if census else ('dry_run' if dry_run else 'live'),
            'selector': {
                'model_types': sorted(model_types) if not census else 'all',
                'since': options['since'],
                'force': options['force'],
                'force_unstamped': options['force_unstamped'],
                'force_stale_profile': options['force_stale_profile'],
            },
            'ai_settings_loaded': ai_settings is not None,
            'profiles': profiles,
            'totals': counts,
            'by_error_code': dict(sorted(by_error_code.items())),
            'by_pipeline': dict(sorted(by_pipeline.items())),
            'by_model_type': dict(sorted(by_model_type.items())),
            'by_embedding_profile': by_profile,
            'out_of_scope_owners': out_of_scope,
        }
        self.stdout.write(json.dumps(report, sort_keys=True))
        if counts['failed']:
            raise CommandError('Some attachments failed to ingest (see stderr)')
