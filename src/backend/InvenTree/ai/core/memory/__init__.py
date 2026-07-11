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
    SemanticCache,
    CacheConfig,
    CachePolicy,
    CachedEntry,
    CacheResult,
    HITLSafetyRules,
    AzureOpenAIEmbeddingProvider,
    LocalEmbeddingProvider,
    create_semantic_cache,
    get_semantic_cache,
)

__all__ = [
    # User Profile
    "UserProfileProvider",
    "get_user_profile_provider",
    # Thread Summary
    "ThreadSummaryProvider",
    "get_thread_summary_provider",
    # Problem-Solution Cache
    "ProblemSolutionProvider",
    "get_problem_solution_provider",
    # Parts Preference
    "PartsPreferenceProvider",
    "get_parts_preference_provider",
    # Semantic Cache
    "SemanticCache",
    "CacheConfig",
    "CachePolicy",
    "CachedEntry",
    "CacheResult",
    "HITLSafetyRules",
    "AzureOpenAIEmbeddingProvider",
    "LocalEmbeddingProvider",
    "create_semantic_cache",
    "get_semantic_cache",
]
