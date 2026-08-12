"""DB-backed user-profile context provider (S35).

Replaces the write-never file-JSON ``UserProfileProvider``: every production
read of that provider returned hardcoded defaults, so the ``user_profile``
context key never carried real data. This provider reads the real
``users.UserProfile`` row — displayname, position, language — the first real
data the key has carried.

The read is one bounded ORM query (indexed pk + ``select_related``) wrapped
in ``sync_to_async``, running under ``gather_context``'s per-provider
timeout. Note the asgiref caveat: a timeout abandons the coroutine but the
query keeps running on the shared sync thread, so the read must stay this
cheap. Any failure or unknown user degrades to an absent context key
upstream — never a default profile.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class DBUserProfileProvider:
    """Read-only provider over ``users.UserProfile``; no write path exists."""

    async def get_profile(self, user_id: Any) -> dict[str, Any] | None:
        """Return the user's profile facts, or None when unresolvable."""
        try:
            user_pk = int(user_id)
        except (TypeError, ValueError):
            # "anonymous" and other non-pk actors simply have no profile.
            return None

        from asgiref.sync import sync_to_async

        return await sync_to_async(self._read, thread_sensitive=True)(user_pk)

    @staticmethod
    def _read(user_pk: int) -> dict[str, Any] | None:
        """Single indexed read; a missing profile row is not an error."""
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.filter(pk=user_pk).select_related("profile").first()
        if user is None:
            return None
        # Profile rows are auto-created by a post_save signal, but the signal
        # is skipped during data import — tolerate an absent row.
        profile = getattr(user, "profile", None)
        display_name = (getattr(profile, "displayname", None) or "").strip()
        return {
            "username": user.username,
            "display_name": display_name or user.get_full_name() or user.username,
            "position": (getattr(profile, "position", None) or "").strip(),
            "language": (getattr(profile, "language", None) or "").strip() or "en",
        }


_provider: DBUserProfileProvider | None = None


def get_user_profile_provider() -> DBUserProfileProvider:
    """Get the shared provider instance (stateless; shared for symmetry only)."""
    global _provider
    if _provider is None:
        _provider = DBUserProfileProvider()
    return _provider


__all__ = ["DBUserProfileProvider", "get_user_profile_provider"]
