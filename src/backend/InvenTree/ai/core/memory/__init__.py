"""
AIMMS Memory Module

Contains memory-related components:
- DBUserProfileProvider: the ``user_profile`` context key, read from the
  real ``users.UserProfile`` model (S35).

The semantic cache that lived here was deleted in S15: it was the latent
serve-machine-A's-diagnosis-for-machine-B trap S6 fenced, and deletion makes
that failure structurally impossible. The file-JSON providers
(thread_summary, parts_preference, the old user_profile) were deleted in
S35: write-never in production, hardcoded-default reads.
"""

from ai.core.memory.providers import (
    DBUserProfileProvider,
    get_user_profile_provider,
)

__all__ = [
    "DBUserProfileProvider",
    "get_user_profile_provider",
]
