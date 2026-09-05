"""
AIMMS Memory Module

Contains memory-related components:
- DBUserProfileProvider: the ``user_profile`` context key, read from the
  real ``users.UserProfile`` model (S35).
- The M1 memory layer (plan of record §9): ``vocabulary``, ``recall_filter``,
  ``token_estimator`` and ``context_assembler`` — one typed ``ContextBundle``
  per turn that every rail renders from (GR-34). Import boundary (GR-35):
  none of those modules import ``agent_framework``; only the
  ``maf_adapter`` package may. ``conversation.py`` and ``providers/``
  pre-date the layer and are outside that boundary until relocated.

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
