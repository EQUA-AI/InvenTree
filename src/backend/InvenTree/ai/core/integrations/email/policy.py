"""Recipient policy for outbound email (voice-UX plan P0-11 / OD-5).

``AIMMS_EMAIL_RECIPIENT_ALLOWLIST`` is a comma-separated list of addresses
and/or ``@domain`` suffixes. Semantics:

- **unset** → the policy is inactive and sending behaves as before;
- **set** (even to an empty string) → every recipient (to/cc/bcc) must match
  an allow-listed address exactly (case-insensitive) or end with an
  allow-listed ``@domain``; otherwise the send is refused *before* any
  provider call and the caller reports ``blocked_by_policy``.

Setting the variable to an empty value is therefore the one-line way to make
a non-production deployment unable to send mail at all. This module is pure
(no Django, no network) so both the AI tool rail and the approvals executor
can share it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

ENV_VAR = "AIMMS_EMAIL_RECIPIENT_ALLOWLIST"
BLOCKED_ERROR = "blocked_by_policy: recipient not in " + ENV_VAR


@dataclass(frozen=True, slots=True)
class RecipientPolicyDecision:
    """Outcome of checking one send against the allow-list."""

    allowed: bool
    policy_active: bool
    blocked: tuple[str, ...] = ()
    recipients: tuple[str, ...] = ()


def recipient_allowlist(environ: dict[str, str] | None = None) -> tuple[str, ...] | None:
    """Return the configured allow-list, or ``None`` when the policy is inactive."""
    env = os.environ if environ is None else environ
    raw = env.get(ENV_VAR)
    if raw is None:
        return None
    entries = []
    for item in raw.split(","):
        entry = item.strip().lower()
        if entry:
            entries.append(entry)
    return tuple(entries)


def normalize_recipients(*groups: str | Iterable[str] | None) -> tuple[str, ...]:
    """Flatten to/cc/bcc inputs (strings, comma-joined strings, lists) to lower-case addresses."""
    out: list[str] = []
    for group in groups:
        if group is None:
            continue
        items = [group] if isinstance(group, str) else list(group)
        for item in items:
            for piece in str(item).split(","):
                address = piece.strip().lower()
                if address:
                    out.append(address)
    return tuple(out)


def _matches(address: str, allowlist: tuple[str, ...]) -> bool:
    for entry in allowlist:
        if entry.startswith("@"):
            if address.endswith(entry):
                return True
        elif address == entry:
            return True
    return False


def check_recipients(
    to: str | Iterable[str] | None,
    cc: str | Iterable[str] | None = None,
    bcc: str | Iterable[str] | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> RecipientPolicyDecision:
    """Decide whether a send to these recipients is permitted by the allow-list."""
    recipients = normalize_recipients(to, cc, bcc)
    allowlist = recipient_allowlist(environ)
    if allowlist is None:
        return RecipientPolicyDecision(allowed=True, policy_active=False, recipients=recipients)
    blocked = tuple(address for address in recipients if not _matches(address, allowlist))
    return RecipientPolicyDecision(
        allowed=not blocked and bool(recipients),
        policy_active=True,
        blocked=blocked,
        recipients=recipients,
    )
