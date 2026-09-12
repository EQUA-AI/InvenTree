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
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

ENV_VAR = "AIMMS_EMAIL_RECIPIENT_ALLOWLIST"
BLOCKED_ERROR = "blocked_by_policy: recipient not in " + ENV_VAR
_ADDRESS = re.compile(
    r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?",
    re.IGNORECASE | re.ASCII,
)


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
        if not isinstance(group, (str, list, tuple)):
            raise ValueError("Recipients must be email addresses or lists of addresses")
        items = [group] if isinstance(group, str) else group
        for item in items:
            if not isinstance(item, str) or any(c in item for c in ("\r", "\n", "\x00")):
                raise ValueError("Invalid recipient header")
            for piece in item.split(","):
                address = piece.strip().lower()
                if address:
                    # Require bare mailbox addresses. Display-name syntax, groups,
                    # comments and quoted local parts can hide extra recipients
                    # from a naive allow-list and are deliberately unsupported.
                    if not _ADDRESS.fullmatch(address):
                        raise ValueError("Use bare email addresses without display names")
                    out.append(address)
    return tuple(out)


def _matches(address: str, allowlist: tuple[str, ...]) -> bool:
    for entry in allowlist:
        if entry.startswith("@"):
            if address.rsplit("@", 1)[1] == entry[1:]:
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
    allowlist = recipient_allowlist(environ)
    try:
        recipients = normalize_recipients(to, cc, bcc)
    except ValueError:
        return RecipientPolicyDecision(
            allowed=False,
            policy_active=allowlist is not None,
            blocked=("invalid recipient syntax",),
        )
    if allowlist is None:
        return RecipientPolicyDecision(allowed=True, policy_active=False, recipients=recipients)
    blocked = tuple(address for address in recipients if not _matches(address, allowlist))
    return RecipientPolicyDecision(
        allowed=not blocked and bool(recipients),
        policy_active=True,
        blocked=blocked,
        recipients=recipients,
    )
