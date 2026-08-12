"""Summarize persisted manual-grounding assessments for the soak review (S27).

Read-only: aggregates the ``metadata['grounding']`` records that the turn
service persists on assistant messages while ``AIMMS_MANUAL_GROUNDING_MODE``
is ``shadow`` or ``enforce``. The output is the evidence base for the human
enforce-flip decision; the command never mutates chat data.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from aichat.models import ChatMessage


class Command(BaseCommand):
    """Report grounding-assessment aggregates and every would-downgrade turn."""

    help = 'Summarize persisted manual-grounding assessments (read-only)'

    def add_arguments(self, parser) -> None:
        """Register the reporting window and output options."""
        parser.add_argument(
            '--days', type=int, default=14, help='Lookback window in days'
        )
        parser.add_argument(
            '--json', action='store_true', help='Emit machine-readable JSON'
        )

    def handle(self, *args, **options) -> None:
        """Aggregate assessments within the window; list downgrade candidates."""
        since = timezone.now() - timedelta(days=max(1, options['days']))
        rows = (
            ChatMessage.objects
            .filter(metadata__has_key='grounding', created_at__gte=since)
            .order_by('created_at')
            .values('id', 'thread_id', 'created_at', 'modality', 'metadata')
        )

        total = 0
        modes: Counter[str] = Counter()
        applied = 0
        heuristic_grounded = 0
        audit_ran = 0
        audit_errors = 0
        audit_verdicts: Counter[str] = Counter()
        citation_counts: Counter[int] = Counter()
        per_day: Counter[str] = Counter()
        cross_machine_turns = 0
        fence_armed_turns = 0
        would_downgrade: list[dict] = []

        for row in rows:
            assessment = row['metadata'].get('grounding')
            if not isinstance(assessment, dict):
                continue
            total += 1
            modes[str(assessment.get('mode'))] += 1
            per_day[row['created_at'].date().isoformat()] += 1
            if assessment.get('applied'):
                applied += 1
                if assessment.get('heuristic_grounded'):
                    heuristic_grounded += 1
                if assessment.get('audit_ran'):
                    audit_ran += 1
                    audit_verdicts[str(assessment.get('audit_grounded'))] += 1
                if assessment.get('audit_error'):
                    audit_errors += 1
                citation_counts[int(assessment.get('citation_count') or 0)] += 1
                if assessment.get('fence_armed'):
                    fence_armed_turns += 1
                if int(assessment.get('cross_machine_count') or 0) > 0:
                    cross_machine_turns += 1
            if assessment.get('would_downgrade'):
                would_downgrade.append({
                    'message_id': row['id'],
                    'thread_id': row['thread_id'],
                    'created_at': row['created_at'].isoformat(),
                    'modality': row['modality'],
                    'downgraded': bool(assessment.get('downgraded')),
                    'citation_count': assessment.get('citation_count'),
                    'cross_machine_count': assessment.get('cross_machine_count', 0),
                    'ungrounded_identifiers': assessment.get(
                        'ungrounded_identifiers', []
                    ),
                })

        report = {
            'window_days': options['days'],
            'since': since.isoformat(),
            'assessed_turns': total,
            'modes': dict(modes),
            'validator_applied': applied,
            'heuristic_grounded': heuristic_grounded,
            'audit_ran': audit_ran,
            'audit_verdicts': dict(audit_verdicts),
            'audit_errors': audit_errors,
            'citation_count_histogram': {
                str(k): v for k, v in sorted(citation_counts.items())
            },
            'per_day': dict(sorted(per_day.items())),
            'fence_armed_turns': fence_armed_turns,
            'cross_machine_turns': cross_machine_turns,
            'would_downgrade_count': len(would_downgrade),
            'would_downgrade': would_downgrade,
        }

        if options['json']:
            self.stdout.write(json.dumps(report, indent=2))
            return

        self.stdout.write(
            f'Grounding soak — last {options["days"]}d '
            f'({total} assessed turns, modes {dict(modes)})'
        )
        self.stdout.write(
            f'  validator applied: {applied} '
            f'(heuristic-grounded {heuristic_grounded}, audit ran {audit_ran}, '
            f'audit errors {audit_errors}, verdicts {dict(audit_verdicts)})'
        )
        self.stdout.write(f'  per-day: {dict(sorted(per_day.items()))}')
        self.stdout.write(
            f'  cross-machine fence: armed on {fence_armed_turns} turns, '
            f'fired on {cross_machine_turns}'
        )
        self.stdout.write(f'  would-downgrade: {len(would_downgrade)}')
        for item in would_downgrade:
            self.stdout.write(
                f'    {item["created_at"]} thread={item["thread_id"]} '
                f'modality={item["modality"]} citations={item["citation_count"]} '
                f'cross_machine={item["cross_machine_count"]} '
                f'ungrounded={item["ungrounded_identifiers"]} '
                f'downgraded={item["downgraded"]}'
            )
