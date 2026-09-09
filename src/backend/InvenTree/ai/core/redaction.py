"""Pre-LLM redaction of memory-path payloads (plan of record §5.9 point 1; GR-06).

Deterministic category families replace credential- and contact-shaped text
with ``[REDACTED:<category>]`` before it reaches a model. Callers get the
per-category hit counts and may log ONLY those — never the matched text.

Stdlib only (no Django, no settings import) so the module runs unchanged in
the pytest island and on the worker. The one shared vocabulary rule with the
S44 settings dump is enforced by a parity test rather than an import:
``config.py`` must stay import-light.

Order of operations in compaction is: redact input -> LLM -> merge protected
fields -> directive scrub. Markers contain none of the directive substrings,
so they survive the output scrub (pinned by a test).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

REDACTED = "[REDACTED:{category}]"

_Replacer = Callable[[re.Match[str], str], str]


def _whole(match: re.Match[str], category: str) -> str:
    return REDACTED.format(category=category)


def _keep_key(match: re.Match[str], category: str) -> str:
    """``password=hunter2`` -> ``password=[REDACTED:password]`` (key kept)."""
    return f"{match.group('key')}{match.group('sep')}{REDACTED.format(category=category)}"


def _keep_scheme_user(match: re.Match[str], category: str) -> str:
    """``postgres://u:p@`` -> ``postgres://u:[REDACTED:url_credentials]@``."""
    return f"://{match.group('user')}:{REDACTED.format(category=category)}@"


#: ``(category, pattern, replacer)``. ORDER IS THE PRIORITY: specific shapes run
#: before generic ones so a JWT is not first eaten by the ``token=`` family.
#: The phone family requires separators and word boundaries so ten-digit
#: serials, part numbers and hashes never match (guarded by the
#: operational-atoms test). Secret groups refuse to match a marker so a second
#: pass is a no-op (idempotence test).
CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str], _Replacer], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]*?(?:-----END[ A-Z]*PRIVATE KEY-----|\Z)"
        ),
        _whole,
    ),
    (
        "connection_string",
        re.compile(r"(?i)\b(?:server|host|data source)=[^;\n]+;[^\n]*?password=[^;\s]+"),
        _whole,
    ),
    (
        "url_credentials",
        re.compile(r"://(?P<user>[^/@\s:]+):(?P<secret>(?!\[REDACTED:)[^/@\s]+)@"),
        _keep_scheme_user,
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        _whole,
    ),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), _whole),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), _whole),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), _whole),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), _whole),
    (
        "sas_signature",
        re.compile(
            r"(?i)(?P<key>(?:[?&;]|\b)(?:sig|sharedaccesskey|accountkey|apikey|api-key))"
            r"(?P<sep>=)(?!\[REDACTED:)[^&;\s]+"
        ),
        _keep_key,
    ),
    (
        "azure_key",
        re.compile(
            r"(?i)(?P<key>\b(?:azure|cognitive|search|storage)[\w-]*[ _-]?key)"
            r"(?P<sep>\s*[:=]\s*)(?!\[REDACTED:)\S{20,}"
        ),
        _keep_key,
    ),
    (
        "password",
        re.compile(
            r"(?i)(?P<key>\b(?:password|passwd|pwd))(?P<sep>\s*(?:\bis\b|\bwas\b|[:=])\s*)(?!\[REDACTED:)\S+"
        ),
        _keep_key,
    ),
    (
        "pin",
        re.compile(
            r"(?i)(?P<key>\bpin(?:\s*code)?)(?P<sep>\s*(?:\bis\b|\bwas\b|[:=])\s*)\d{4,8}\b"
        ),
        _keep_key,
    ),
    (
        "otp",
        re.compile(
            r"(?i)(?P<key>\b(?:otp|mfa|2fa|one[- ]time|verification|auth(?:enticator)?)"
            r"(?:\s*(?:code|token))?)(?P<sep>\s*(?:\bis\b|\bwas\b|[:=])\s*)\d{4,8}\b"
        ),
        _keep_key,
    ),
    (
        "token",
        re.compile(
            r"(?i)(?P<key>\b(?:token|secret|api[_ -]?key|bearer))(?P<sep>\s*[:=]?\s+|\s*[:=]\s*)"
            r"(?=\S{8,})[A-Za-z0-9._~+/=-]{8,}"
        ),
        _keep_key,
    ),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), _whole),
    (
        "phone",
        re.compile(
            r"(?<![\w-])(?:\+\d{1,3}[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}(?![\w-])"
        ),
        _whole,
    ),
)

CATEGORIES: tuple[str, ...] = tuple(category for category, _, _ in CATEGORY_PATTERNS)


@dataclass(frozen=True)
class RedactionResult:
    """A redacted payload and the per-category hit counts (content-free)."""

    value: Any
    counts: Mapping[str, int]

    @property
    def redacted(self) -> bool:
        return bool(self.counts)


def redact_text(text: str) -> tuple[str, Counter[str]]:
    """Redact one string; returns the new text and the hits per category."""
    counts: Counter[str] = Counter()
    for category, pattern, replacer in CATEGORY_PATTERNS:

        def _sub(
            match: re.Match[str], _category: str = category, _replacer: _Replacer = replacer
        ) -> str:
            counts[_category] += 1
            return _replacer(match, _category)

        text = pattern.sub(_sub, text)
    return text, counts


def redact_payload(obj: Any) -> RedactionResult:
    """Recursively redact every ``str`` leaf of dicts/lists/tuples.

    Keys are never touched (they are schema, not content); non-string scalars
    pass through unchanged.
    """
    counts: Counter[str] = Counter()

    def _walk(value: Any) -> Any:
        if isinstance(value, str):
            redacted, hits = redact_text(value)
            counts.update(hits)
            return redacted
        if isinstance(value, dict):
            return {key: _walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(_walk(item) for item in value)
        return value

    return RedactionResult(value=_walk(obj), counts=dict(counts))


def format_counts(counts: Mapping[str, int]) -> str:
    """``password=2,email=1`` in category order — the only thing a log may carry."""
    return ",".join(
        f"{category}={counts[category]}" for category in CATEGORIES if counts.get(category)
    )


# --------------------------------------------------------------------------- #
# Entropy shadow (M2 PR 4; plan of record §5.9 — count only, no config flag)  #
# --------------------------------------------------------------------------- #

#: Token shapes a compaction payload legitimately carries at high entropy.
#: Matched with ``fullmatch`` against a whitespace-delimited token and against
#: the same token with one leading ``key=``/``key:`` prefix and trailing
#: punctuation stripped, so ``content_hash=<sha256>`` and ``(SN-1234567A),``
#: are atoms too. ORDER IS NOT SIGNIFICANT: any match exempts the token.
OPERATIONAL_ATOM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # sha256 / sha1 / md5 hex digests.
    ("hex_digest", re.compile(r"(?i)[0-9a-f]{64}|[0-9a-f]{40}|[0-9a-f]{32}")),
    # Container Apps revision ids: ``--0000065`` or ``aimms-dev--0000065``.
    ("revision_id", re.compile(r"[a-z0-9-]*--\d{7}")),
    # ISO-8601 dates and timestamps, optional fraction and offset.
    (
        "iso_timestamp",
        re.compile(
            r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
        ),
    ),
    # Serials, part numbers, work-order refs: upper-case/digits/dashes with at
    # least one digit and one letter.
    ("serial_ref", re.compile(r"(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9-]{6,}")),
    # Citation keys: ``manual:pump3:seals``-style colon-separated atoms of
    # lower-case segments (a mixed-case segment is the blob shape, not a
    # key). Judged on the prefix-stripped remainder only, see below.
    ("citation_key", re.compile(r"[a-z0-9_.-]+(?::[a-z0-9_.-]+)+")),
)

#: Atom families judged only on the remainder after the ``key=``/``key:``
#: prefix strip, never on the raw token. The citation shape itself contains
#: the prefix delimiter, so a raw-token match would exempt every
#: ``label:<blob>`` — precisely the class the families do not name and the
#: shadow exists to count. ``manual:pump3:seals`` still matches on its
#: remainder ``pump3:seals``.
REMAINDER_ONLY_ATOMS: frozenset[str] = frozenset({"citation_key"})

_TOKEN_PREFIX = re.compile(r"^[\w-]+[=:]")
_TOKEN_TRIM = ".,;:!?)(\"'[]{}<>"


def entropy_bits(token: str) -> float:
    """Shannon entropy of ``token`` in bits per character (0.0 for empty)."""
    if not token:
        return 0.0
    counts = Counter(token)
    length = len(token)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def is_operational_atom(token: str) -> bool:
    """True when the token (raw, or key-prefix/punctuation stripped) is an atom.

    Families in ``REMAINDER_ONLY_ATOMS`` see only the prefix-stripped
    remainder; every other family sees the raw token, the punctuation-
    stripped token and the remainder.
    """
    stripped = token.strip(_TOKEN_TRIM)
    remainder = _TOKEN_PREFIX.sub("", stripped, count=1).strip(_TOKEN_TRIM)
    for name, pattern in OPERATIONAL_ATOM_PATTERNS:
        candidates = (remainder,) if name in REMAINDER_ONLY_ATOMS else (token, stripped, remainder)
        if any(pattern.fullmatch(candidate) for candidate in candidates if candidate):
            return True
    return False


def entropy_flags(obj: Any, *, min_len: int = 20, threshold_bits: float = 4.5) -> int:
    """Count the high-entropy tokens a redacted payload still carries.

    The object is redacted first (idempotent on an already-redacted value),
    then every whitespace-delimited token of every string leaf is counted
    when it is at least ``min_len`` characters, is not a ``[REDACTED:...]``
    marker, is not an operational atom and has more than ``threshold_bits``
    of Shannon entropy per character. A shadow measurement of what the
    category families miss (plan §5.9): the count is the whole output, the
    tokens are never returned or logged.
    """
    flags = 0

    def _walk(value: Any) -> None:
        nonlocal flags
        if isinstance(value, str):
            for token in value.split():
                if len(token) < min_len or REDACTED[: REDACTED.index("{")] in token:
                    continue
                if is_operational_atom(token):
                    continue
                if entropy_bits(token) > threshold_bits:
                    flags += 1
        elif isinstance(value, dict):
            for item in value.values():
                _walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                _walk(item)

    _walk(redact_payload(obj).value)
    return flags


__all__ = [
    "CATEGORIES",
    "CATEGORY_PATTERNS",
    "OPERATIONAL_ATOM_PATTERNS",
    "REDACTED",
    "REMAINDER_ONLY_ATOMS",
    "RedactionResult",
    "entropy_bits",
    "entropy_flags",
    "format_counts",
    "is_operational_atom",
    "redact_payload",
    "redact_text",
]
