"""Thread-stable identity pseudonyms for AI projections (S5b, Q15/A16).

The decision record's identity rule: ``assigned_to``/``performed_by`` and
other personal identities are omitted from AI projections by default and
rendered as role labels; where DISTINCTION matters (an event sequence where
"the same person moved it twice" is the signal), a server-side pseudonym
that is stable only within one thread's conversation is used instead.

Mechanics: an HMAC chain — a pepper derived from ``SECRET_KEY`` (never
stored, never logged) keyed with the thread pk, then the subject. The same
person therefore reads as the same ``person-xxxxxxxxxx`` within a thread and
as an unrelated label in any other thread, export, or tenant — the
"not correlatable" property comes from the thread pk living inside the
derivation, and the "no stored salt" property from deriving everything.

Documented trade-off: rotating ``SECRET_KEY`` changes pseudonyms mid-thread.
These are display-only labels, so that is acceptable — and preferable to a
stored mapping table that would itself be identity data.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

_DERIVE_INFO = b"aimms.pseudonym.v1"


def _pepper() -> bytes:
    from django.conf import settings

    secret = str(getattr(settings, "SECRET_KEY", "") or "aimms-fallback")
    return hmac.new(secret.encode("utf-8"), _DERIVE_INFO, hashlib.sha256).digest()


def thread_pseudonymizer(thread_pk: int | None) -> Callable[[str, object], str]:
    """A ``(kind, subject) -> "person-…"`` labeler stable within one thread.

    ``kind`` partitions namespaces ("user", "text") so a user pk can never
    collide with a free-text name; ``subject`` is the pk or normalized text.
    A None thread (no conversation context) still produces stable-in-call
    labels under the thread key ``"none"``.
    """
    thread_key = hmac.new(
        _pepper(),
        str(thread_pk if thread_pk is not None else "none").encode("utf-8"),
        hashlib.sha256,
    ).digest()

    def label(kind: str, subject: object) -> str:
        digest = hmac.new(thread_key, f"{kind}:{subject}".encode(), hashlib.sha256).hexdigest()
        return f"person-{digest[:10]}"

    return label


__all__ = ["thread_pseudonymizer"]
