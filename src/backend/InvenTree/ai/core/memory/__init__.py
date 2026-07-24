"""
AIMMS Memory Module

Contains memory-related components:
- ContextProviders: MAF-compliant context injection
  - UserProfileProvider: User preferences and history
  - ThreadSummaryProvider: Conversation summarization
  - ProblemSolutionProvider: Semantic problem-solution cache
  - PartsPreferenceProvider: Part selection preferences

- SemanticCache: Problem-solution caching with HITL safety rules
- FoundryStore: Azure Foundry Memory Store integration (TODO)
"""

from ai.core.memory.providers import (
    PartsPreferenceProvider,
    ProblemSolutionProvider,
    ThreadSummaryProvider,
    UserProfileProvider,
    get_parts_preference_provider,
    get_problem_solution_provider,
    get_thread_summary_provider,
    get_user_profile_provider,
)
from ai.core.memory.semantic_cache import (
    AzureOpenAIEmbeddingProvider,
    CacheConfig,
    CachedEntry,
    CachePolicy,
    CacheResult,
    HITLSafetyRules,
    LocalEmbeddingProvider,
    SemanticCache,
    create_semantic_cache,
    get_semantic_cache,
)

__all__ = [
    "AzureOpenAIEmbeddingProvider",
    "CacheConfig",
    "CachePolicy",
    "CacheResult",
    "CachedEntry",
    "HITLSafetyRules",
    "LocalEmbeddingProvider",
    # Parts Preference
    "PartsPreferenceProvider",
    # Problem-Solution Cache
    "ProblemSolutionProvider",
    # Semantic Cache
    "SemanticCache",
    # Thread Summary
    "ThreadSummaryProvider",
    # User Profile
    "UserProfileProvider",
    "create_semantic_cache",
    "get_parts_preference_provider",
    "get_problem_solution_provider",
    "get_semantic_cache",
    "get_thread_summary_provider",
    "get_user_profile_provider",
]
