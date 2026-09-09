"""M2 PR 3 stale-fact probe: counts over a sample of thread summaries.

Read-only and value-free: the command prints counts only — never a
summary text, an item text or a thread id. The M2 exit note records
``heuristic_stale`` before the delta-ops image and ``heuristic_stale``
plus ``superseded_items`` after it, so the two reads compare like with
like (plan §8.7, §8.8 "stale-fact rate").
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from ai.core.memory.summary_body import (
    ITEM_LISTS,
    active_items,
    item_lifecycle,
    item_text,
    normalize_text,
    parse_summary,
)
from ai.core.memory.vocabulary import FactLifecycle

#: Minimum token length for the overlap heuristic (drops "a", "is", "of").
_MIN_TOKEN = 3
#: Overlap ratio at or above which an active machine fact reads as stale.
_STALE_OVERLAP = 0.5

#: Output keys in print order.
_KEYS = (
    'threads_sampled',
    'threads_unparsed',
    'bodies_v2',
    'machine_facts_active',
    'corrections_items',
    'superseded_items',
    'forgotten_items',
    'heuristic_stale',
)


def _tokens(text: str) -> set[str]:
    return {t for t in normalize_text(text).split() if len(t) >= _MIN_TOKEN}


def _items(body: dict, field: str) -> list:
    value = body.get(field)
    return list(value) if isinstance(value, list) else []


def probe_bodies(summaries) -> dict[str, int]:
    """Fold the sampled summaries into the count dictionary."""
    counts = dict.fromkeys(_KEYS, 0)
    for summary in summaries:
        counts['threads_sampled'] += 1
        _label, body = parse_summary(summary)
        if not body:
            counts['threads_unparsed'] += 1
            continue
        if body.get('body_version') == 2:
            counts['bodies_v2'] += 1
        facts = active_items(body, 'machine_facts')
        counts['machine_facts_active'] += len(facts)
        corrections = _items(body, 'corrections')
        counts['corrections_items'] += len(corrections)
        for field in ITEM_LISTS:
            for item in _items(body, field):
                lifecycle = item_lifecycle(item)
                if lifecycle == FactLifecycle.SUPERSEDED:
                    counts['superseded_items'] += 1
                elif lifecycle == FactLifecycle.FORGOTTEN:
                    counts['forgotten_items'] += 1
        correction_tokens = [_tokens(item_text(c)) for c in corrections]
        correction_tokens = [c for c in correction_tokens if c]
        for fact in facts:
            fact_tokens = _tokens(item_text(fact))
            if not fact_tokens:
                continue
            if any(
                len(fact_tokens & c) / len(fact_tokens) >= _STALE_OVERLAP
                for c in correction_tokens
            ):
                counts['heuristic_stale'] += 1
    return counts


class Command(BaseCommand):
    """Print stale-fact counts over the most recently updated summaries."""

    help = (
        'Sample the most recently updated thread summaries and print counts '
        '(v2 bodies, active machine facts, corrections, superseded/forgotten '
        'items, heuristic stale facts). Read-only; never prints text or ids.'
    )

    def add_arguments(self, parser):
        """Register the sample size and the JSON switch."""
        parser.add_argument('--sample', type=int, default=30)
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        """Read the sample, fold the counts, print them."""
        from aichat.models import ChatThread

        sample = max(0, int(options.get('sample') or 0))
        rows = (
            ChatThread.objects
            .exclude(summary='')
            .order_by('-updated_at')
            .values_list('pk', 'summary')[:sample]
        )
        counts = probe_bodies(summary for _pk, summary in rows)
        if options.get('as_json'):
            self.stdout.write(json.dumps(counts))
            return
        for key in _KEYS:
            self.stdout.write(f'{key} = {counts[key]}')
