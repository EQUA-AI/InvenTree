"""
AIMMS Memory Module

Contains memory-related components:
- ContextProviders: MAF-compliant context injection
  - UserProfileProvider: User preferences and history
  - ThreadSummaryProvider: Conversation summarization
  - PartsPreferenceProvider: Part selection preferences

The semantic cache that lived here was deleted in S15: it was the latent
serve-machine-A's-diagnosis-for-machine-B trap S6 fenced, and deletion makes
that failure structurally impossible.
"""

from ai.core.memory.providers import (
    PartsPreferenceProvider,
    ThreadSummaryProvider,
    UserProfileProvider,
    get_parts_preference_provider,
    get_thread_summary_provider,
    get_user_profile_provider,
)

__all__ = [
    # Parts Preference
    "PartsPreferenceProvider",
    # Thread Summary
    "ThreadSummaryProvider",
    # User Profile
    "UserProfileProvider",
    "get_parts_preference_provider",
    "get_thread_summary_provider",
    "get_user_profile_provider",
]
