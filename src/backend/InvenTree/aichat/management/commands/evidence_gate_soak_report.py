"""Summarize persisted evidence-gate shadow scans for the soak review (S10).

Read-only: aggregates the ``metadata['evidence_gate']`` blobs the turn
pipeline persists on assistant messages while ``AIMMS_EVIDENCE_GATE_MODE``
is ``shadow`` (the legacy-rail prose scans AND the analysis-route dark
rehearsals). The output — would-fail rates per code, per intent — is the
evidence base for the human enforce-flip decision; the command never
mutates chat data and every aggregate is content-free by construction
(the blobs carry codes and counts only, never tokens or text).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from aichat.models import ChatMessage


class Command(BaseCommand):
    """Report evidence-gate shadow aggregates (read-only)."""

    help = 'Summarize persisted evidence-gate shadow scans (read-only)'

    def add_arguments(self, parser) -> None:
        """Register the reporting window and output options."""
        parser.add_argument(
            '--days', type=int, default=14, help='Lookback window in days'
        )
        parser.add_argument(
            '--json', action='store_true', help='Emit machine-readable JSON'
        )

    def handle(self, *args, **options) -> None:
        """Aggregate scans and rehearsal verdicts within the window."""
        since = timezone.now() - timedelta(days=max(1, options['days']))
        rows = (
            ChatMessage.objects
            .filter(metadata__has_key='evidence_gate', created_at__gte=since)
            .order_by('created_at')
            .values('id', 'created_at', 'metadata')
        )

        total = 0
        prose_scans = 0
        rehearsals = 0
        would_fail_turns = 0
        codes: Counter[str] = Counter()
        intents: Counter[str] = Counter()
        rehearsal_verdicts: Counter[str] = Counter()
        per_day: Counter[str] = Counter()

        for row in rows:
            blob = row['metadata'].get('evidence_gate')
            if not isinstance(blob, dict):
                continue
            total += 1
            per_day[row['created_at'].date().isoformat()] += 1
            if blob.get('mode') == 'shadow_rehearsal':
                rehearsals += 1
                rehearsal_verdicts[str(blob.get('verdict') or 'unknown')] += 1
                continue
            prose_scans += 1
            intents[str(blob.get('intent') or 'unknown')] += 1
            fail_codes = blob.get('would_fail') or []
            if fail_codes:
                would_fail_turns += 1
            for code in fail_codes:
                codes[str(code)] += 1

        report = {
            'window_days': max(1, options['days']),
            'turns_with_gate_blobs': total,
            'prose_scans': prose_scans,
            'prose_would_fail_turns': would_fail_turns,
            'prose_would_fail_rate': (
                round(would_fail_turns / prose_scans, 3) if prose_scans else None
            ),
            'would_fail_codes': dict(codes.most_common()),
            'intents': dict(intents.most_common()),
            'rehearsals': rehearsals,
            'rehearsal_verdicts': dict(rehearsal_verdicts.most_common()),
            'per_day': dict(sorted(per_day.items())),
        }
        if options['json']:
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
            return
        self.stdout.write(f'evidence-gate soak, last {report["window_days"]} days')
        self.stdout.write(f'  turns with gate blobs: {total}')
        self.stdout.write(
            f'  prose scans: {prose_scans} '
            f'(would-fail: {would_fail_turns}, rate: {report["prose_would_fail_rate"]})'
        )
        for code, count in codes.most_common():
            self.stdout.write(f'    {code}: {count}')
        self.stdout.write(f'  rehearsals: {rehearsals}')
        for verdict, count in rehearsal_verdicts.most_common():
            self.stdout.write(f'    {verdict}: {count}')
