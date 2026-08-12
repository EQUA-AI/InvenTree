"""AIMMS Context Providers.

S35: the file-JSON providers (user_profile, thread_summary,
parts_preference) were deleted — they were write-never in production and
every read returned hardcoded defaults. The one context key that survives,
``user_profile``, is now backed by the real ``users.UserProfile`` model.
Thread summaries return in S38 as ``ChatThread.summary`` compaction, injected
by the turn service rather than a context provider.
"""

from ai.core.memory.providers.db_user_profile import (
    DBUserProfileProvider,
    get_user_profile_provider,
)

__all__ = [
    "DBUserProfileProvider",
    "get_user_profile_provider",
]
