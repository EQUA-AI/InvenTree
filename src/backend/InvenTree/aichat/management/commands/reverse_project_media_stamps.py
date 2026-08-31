"""Reverse-project media stamps from the live Search index into Postgres.

Closes the media half of the 0031 gap: rows indexed before WP-B wired the
columns have ``indexed_at IS NULL`` in Postgres while the live media index
documents carry the real value (the old projection always wrote it to the
index — verified live 2026-08-31). A forced re-ingest would repair the same
rows but re-captions every segment (gpt-4o has no temperature or seed), and
the caption is the media index's primary searchable field — so the exact,
zero-provider-call repair is to read the stamp BACK.

Writes ONLY ``indexed_at`` (and ``media_recorded_at`` when the index carries
a non-null ``recorded_at`` — today it never does), through a
``filter(indexed_at__isnull=True).update(...)`` so the command is idempotent
and race-safe, and via queryset update so ``auto_now`` never bumps
``updated_at`` (the drift ``indexed_at`` exists to escape).
"""

import json
import re

from django.core.management.base import BaseCommand, CommandError

_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')


class Command(BaseCommand):
    """Stamp unstamped INDEXED media rows from their own live documents."""

    help = (
        'Reverse-project indexed_at (and recorded_at when present) from the '
        'live media Search index onto INDEXED AttachmentIngest rows that '
        'pre-date the 0031 write path. Exact, idempotent, no provider calls.'
    )

    def add_arguments(self, parser):
        """Register the preview flag."""
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be stamped, change nothing',
        )

    def handle(self, *args, **options):
        """Walk the unstamped media rows and copy stamps from the index."""
        from datetime import datetime

        from django.conf import settings as django_settings
        from django.utils import timezone as django_tz

        from aichat.models import AttachmentIngest, AttachmentIngestState

        def _parse(value: str) -> datetime:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if not django_settings.USE_TZ and django_tz.is_aware(parsed):
                # The test runner flips USE_TZ off (InvenTree/settings.py):
                # columns read back naive there while production reads aware.
                # Store the same INSTANT either way.
                parsed = django_tz.make_naive(parsed, django_tz.timezone.utc)
            return parsed

        rows = list(
            AttachmentIngest.objects.filter(
                state=AttachmentIngestState.INDEXED,
                pipeline__in=('image', 'video'),
                indexed_at__isnull=True,
            ).order_by('pk')
        )
        self.stdout.write(f'selected: {len(rows)}')
        report: list[dict] = []
        failures = 0
        if rows:
            from ai.core.integrations.attachment_search import MediaSearchProjection

            # from_settings() only: the index-alias guard lives there and a
            # direct constructor would bypass it.
            projection = MediaSearchProjection.from_settings()
            try:
                client = projection.client()
                for row in rows:
                    entry = {
                        'ingest_id': row.pk,
                        'attachment_id': row.attachment_id,
                        'pipeline': row.pipeline,
                    }
                    report.append(entry)
                    if not _SHA256_RE.fullmatch(row.source_sha256 or ''):
                        entry['outcome'] = 'invalid_sha'
                        failures += 1
                        continue
                    try:
                        docs = list(
                            client.search(
                                search_text='*',
                                filter=(
                                    f'attachment_id eq {int(row.attachment_id)}'
                                    f" and source_sha256 eq '{row.source_sha256}'"
                                    ' and is_current eq true'
                                ),
                                select=['id', 'indexed_at', 'recorded_at'],
                                top=1000,
                            )
                        )
                    except Exception:
                        # Value-free: provider errors can carry endpoint values.
                        entry['outcome'] = 'search_failed'
                        failures += 1
                        continue
                    stamps = {str(doc.get('indexed_at') or '') for doc in docs}
                    stamps.discard('')
                    if not docs or not stamps:
                        # The row claims INDEXED but the index has nothing
                        # live for it. Never invent a timestamp — report it
                        # loudly; a forced re-ingest is that row's repair.
                        entry['outcome'] = 'no_live_documents'
                        failures += 1
                        continue
                    if len(stamps) > 1:
                        # All segments of one ingest share one indexed_at;
                        # disagreement means the index holds mixed writes.
                        entry['outcome'] = 'conflicting_index_docs'
                        entry['distinct_stamps'] = len(stamps)
                        failures += 1
                        continue
                    try:
                        fields = {'indexed_at': _parse(next(iter(stamps)))}
                    except ValueError:
                        entry['outcome'] = 'bad_timestamp'
                        failures += 1
                        continue
                    recorded = {str(doc.get('recorded_at') or '') for doc in docs}
                    recorded.discard('')
                    if len(recorded) == 1:
                        try:
                            fields['media_recorded_at'] = _parse(next(iter(recorded)))
                        except ValueError:
                            entry['recorded_at_unparseable'] = True
                    elif len(recorded) > 1:
                        # Nullable and null today everywhere; skip rather
                        # than guess, and say so.
                        entry['recorded_at_conflict'] = True
                    entry['indexed_at'] = fields['indexed_at'].isoformat()
                    if options['dry_run']:
                        entry['outcome'] = 'would_stamp'
                        continue
                    updated = AttachmentIngest.objects.filter(
                        pk=row.pk, indexed_at__isnull=True
                    ).update(**fields)
                    entry['outcome'] = 'stamped' if updated else 'raced'
            finally:
                projection.close()
        self.stdout.write(
            json.dumps(
                {'selected': len(rows), 'dry_run': options['dry_run'], 'rows': report},
                sort_keys=True,
            )
        )
        if failures:
            raise CommandError(
                'Some media rows could not be reverse-projected (see report)'
            )
