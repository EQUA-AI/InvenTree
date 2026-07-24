"""
AIMMS Context Providers

MAF-compliant ContextProvider implementations following the interface:
- invoking(messages, **kwargs) -> Context
- invoked(request_messages, response_messages, invoke_exception, **kwargs)
"""

from ai.core.memory.providers.parts_preference import (
    PartsPreferenceProvider,
    get_parts_preference_provider,
)
from ai.core.memory.providers.problem_solution import (
    ProblemSolutionProvider,
    get_problem_solution_provider,
)
from ai.core.memory.providers.thread_summary import (
    ThreadSummaryProvider,
    get_thread_summary_provider,
)
from ai.core.memory.providers.user_profile import (
    UserProfileProvider,
    get_user_profile_provider,
)

__all__ = [
    # Parts Preference
    "PartsPreferenceProvider",
    # Problem-Solution Cache
    "ProblemSolutionProvider",
    # Thread Summary
    "ThreadSummaryProvider",
    # User Profile
    "UserProfileProvider",
    "get_parts_preference_provider",
    "get_problem_solution_provider",
    "get_thread_summary_provider",
    "get_user_profile_provider",
]
