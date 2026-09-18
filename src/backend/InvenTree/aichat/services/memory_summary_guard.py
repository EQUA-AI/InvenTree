"""Apply durable deletion proof to later summary compaction without storing text.

Typed-slot suppression remains in the durable fact writer. This extra summary
guard detects normalized exact claims, including claims embedded in longer prose.
It does not claim semantic paraphrase detection. Unknown/oversized proof or a
comparison budget breach withholds the summary rather than skipping the guard.
"""

from dataclasses import dataclass

from ai.core.memory.summary_body import ITEM_LISTS, item_text, normalize_text
from aichat.models import MemoryFact, MemoryFactTombstone
from aichat.services.memory_lifecycle import claim_fingerprint

MAX_PROOF = 1000
MAX_COMPARISONS = 10_000


@dataclass(frozen=True)
class SummaryMemoryGuard:
    """Only keyed identities and a conservative overflow marker leave storage."""

    fingerprints: frozenset[str] = frozenset()
    fact_ids: frozenset[str] = frozenset()
    overflow: bool = False


def load_guard(owner_id):
    """Take a bounded proof snapshot; owner/thread CAS protects concurrent forget."""
    stones = list(
        MemoryFactTombstone.objects
        .filter(owner_id=owner_id)
        .order_by('-deleted_at', '-id')
        .values('id', 'fact_id', 'claim_fingerprint', 'deleted_at')[: MAX_PROOF + 1]
    )
    if len(stones) > MAX_PROOF:
        return SummaryMemoryGuard(overflow=True)
    if not stones:
        return SummaryMemoryGuard()
    newest = {}
    for row in stones:
        newest.setdefault(row['claim_fingerprint'], row)
    # Only a newly confirmed explicit revival permits a claim to return. The
    # old row remains blocked by its own ID even when a new row is allowed.
    revived = MemoryFact.objects.filter(
        owner_id=owner_id,
        lifecycle_state='active',
        origin='user_explicit',
        confirming_proposal__state='executed',
        revives__in=[row['id'] for row in stones],
    ).values('claim_fingerprint', 'revives', 'created_at')[: MAX_PROOF + 1]
    permitted = set()
    for fact in revived:
        stone = newest.get(fact['claim_fingerprint'])
        if (
            stone
            and fact['revives'] == stone['id']
            and fact['created_at'] > stone['deleted_at']
        ):
            permitted.add(fact['claim_fingerprint'])
    return SummaryMemoryGuard(
        frozenset(newest) - permitted,
        frozenset(str(row['fact_id']) for row in stones if row['fact_id']),
    )


def source_is_blocked(metadata, guard):
    """Do not re-summarize an answer known to have used a deleted fact row."""
    if guard.overflow:
        return True
    if not isinstance(metadata, dict):
        return False
    identities = metadata.get('memory_fact_ids', [])
    if not isinstance(identities, list) or len(identities) > 12:
        return True
    return any(
        not isinstance(identity, str) or identity in guard.fact_ids
        for identity in identities
    )


def scrub_body(body, guard):
    """Remove complete matching items and clear narrative after any match."""
    if not guard.fingerprints and not guard.overflow:
        return body, 0
    comparisons = 0

    def matches(text):
        nonlocal comparisons
        words = normalize_text(text).split()
        # Try the whole claim first, then bounded normalized contiguous spans.
        for size in range(len(words), 0, -1):
            for start in range(len(words) - size + 1):
                comparisons += 1
                if comparisons > MAX_COMPARISONS:
                    raise OverflowError
                if (
                    claim_fingerprint(' '.join(words[start : start + size]))
                    in guard.fingerprints
                ):
                    return True
        return False

    try:
        if guard.overflow:
            raise OverflowError
        hits = 0
        for field in ITEM_LISTS:
            kept = []
            for item in body.get(field, []):
                if matches(item_text(item)):
                    hits += 1
                else:
                    kept.append(item)
            body[field] = kept
        for field in ('narrative', 'label'):
            if matches(str(body.get(field) or '')):
                body[field] = ''
                hits += 1
        if hits:
            body['narrative'] = ''
        return body, hits
    except (OverflowError, ValueError):
        for field in ITEM_LISTS:
            body[field] = []
        body['narrative'] = body['label'] = ''
        body['citation_keys'] = []
        return body, 1


def text_is_blocked(text, guard):
    """Withhold exact forgotten claims before sending transcript to the model."""
    _, hits = scrub_body({'machine_facts': [text]}, guard)
    return bool(hits)
