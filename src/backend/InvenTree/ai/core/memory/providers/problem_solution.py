"""
Problem-Solution Context Provider

MAF-compliant ContextProvider for semantic problem-solution cache.
Retrieves relevant past solutions for similar problems.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from ai.core.config import settings

logger = structlog.get_logger(__name__)


class ProblemSolutionProvider:
    """
    Context provider for problem-solution cache.

    Implements MAF ContextProvider interface:
    - invoking(messages, **kwargs) -> Context
    - invoked(request_messages, response_messages, invoke_exception, **kwargs)

    Provides:
    - Similar past problems and their solutions
    - Confidence scores for matches
    - HITL-safe decisions (won't short-circuit write operations)

    The cache is populated after successful problem resolutions.
    """

    # Configuration
    SIMILARITY_THRESHOLD = 0.92  # From settings
    MAX_CACHE_ENTRIES = 1000

    def __init__(self, cache_dir: Path | None = None) -> None:
        """
        Initialize the problem-solution provider.

        Args:
            cache_dir: Directory for cache files.
        """
        self.cache_dir = cache_dir or (settings.data_dir / "problem_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "problem_solutions.json"
        self._cache: list[dict[str, Any]] = []
        self._load_cache()
        logger.info(
            "ProblemSolutionProvider initialized",
            cache_dir=str(self.cache_dir),
            entry_count=len(self._cache),
        )

    def _load_cache(self) -> None:
        """Load cache from file."""
        if self.cache_file.exists():
            try:
                with Path(self.cache_file).open() as f:
                    self._cache = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load problem cache", error=str(e))
                self._cache = []

    def _save_cache(self) -> None:
        """Save cache to file."""
        try:
            with Path(self.cache_file).open("w") as f:
                json.dump(self._cache, f, indent=2, default=str)
        except OSError as e:
            logger.error("Failed to save problem cache", error=str(e))

    async def find_similar(
        self,
        problem_text: str,
        threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        Find similar past problems.

        Args:
            problem_text: The current problem description.
            threshold: Similarity threshold (default from settings).

        Returns:
            List of similar problems with solutions.
        """
        threshold = threshold or settings.semantic_cache_similarity_threshold

        # In production, this would use embeddings and vector similarity
        # For now, use simple keyword matching as a placeholder
        similar = []

        problem_words = set(problem_text.lower().split())

        for entry in self._cache:
            cached_problem = entry.get("problem", "")
            cached_words = set(cached_problem.lower().split())

            # Calculate Jaccard similarity
            intersection = len(problem_words & cached_words)
            union = len(problem_words | cached_words)
            similarity = intersection / union if union > 0 else 0

            if similarity >= threshold:
                similar.append({
                    **entry,
                    "similarity": similarity,
                })

        # Sort by similarity (highest first)
        similar.sort(key=lambda x: x["similarity"], reverse=True)

        return similar[:5]  # Return top 5

    async def add_solution(
        self,
        problem: str,
        solution: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Add a problem-solution pair to the cache.

        Args:
            problem: The problem description.
            solution: The solution that worked.
            metadata: Additional metadata (workflow, user, etc.).
        """
        entry = {
            "problem": problem,
            "solution": solution,
            "metadata": metadata or {},
            "created_at": datetime.now(UTC).isoformat(),
            "usage_count": 0,
        }

        self._cache.insert(0, entry)

        # Limit cache size
        if len(self._cache) > self.MAX_CACHE_ENTRIES:
            self._cache = self._cache[: self.MAX_CACHE_ENTRIES]

        self._save_cache()

        logger.debug("Added problem-solution to cache", problem_preview=problem[:50])

    def may_short_circuit(self, **kwargs: Any) -> bool:
        """
        Determine if cache can short-circuit the workflow.

        HITL-safe: Never short-circuit if operation involves writes.

        Args:
            **kwargs: Context including workflow info.

        Returns:
            True if caching is safe, False if must execute workflow.
        """
        # Check for write operations
        write_operation = kwargs.get("write_operation", False)
        if write_operation:
            logger.debug("Cache short-circuit disabled: write operation")
            return False

        # Check workflow type
        workflow_id = kwargs.get("workflow_id", "")
        hitl_workflows = {"wf4", "wf5"}  # Procurement and CPQ need HITL
        if workflow_id in hitl_workflows:
            logger.debug("Cache short-circuit disabled: HITL workflow", workflow_id=workflow_id)
            return False

        return True

    async def invoking(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Called before agent invocation.

        Retrieves similar past problems for context.

        Args:
            messages: The conversation messages.
            **kwargs: Additional context.

        Returns:
            Context dictionary with similar problems.
        """
        if not settings.semantic_cache_enabled:
            return {"cache_enabled": False}

        # Extract the current problem from the last user message
        user_messages = [m for m in messages if m.get("role") == "user"]
        if not user_messages:
            return {"similar_problems": []}

        current_problem = user_messages[-1].get("content", "")
        if isinstance(current_problem, list):
            # Handle multi-part messages
            current_problem = " ".join(
                p.get("text", "") for p in current_problem if p.get("type") == "text"
            )

        # Find similar problems
        similar = await self.find_similar(current_problem)

        # Check if we can use cached solution
        can_short_circuit = self.may_short_circuit(**kwargs)

        if similar and can_short_circuit:
            best_match = similar[0]
            context = {
                "similar_problems": similar,
                "best_match": best_match,
                "can_use_cached": True,
                "cached_solution_hint": f"""
A similar problem was solved before (similarity: {best_match["similarity"]:.0%}):

Previous Problem: {best_match["problem"][:200]}

Previous Solution: {best_match["solution"][:500]}

Consider using this solution if the current problem is similar enough.
""".strip(),
            }
        else:
            context = {
                "similar_problems": similar,
                "can_use_cached": False,
                "reason": "No similar problems found"
                if not similar
                else "Write operation or HITL workflow",
            }

        logger.debug(
            "ProblemSolutionProvider.invoking",
            similar_count=len(similar),
            can_use_cached=context.get("can_use_cached", False),
        )

        return context

    async def invoked(
        self,
        request_messages: list[dict[str, Any]],
        response_messages: list[dict[str, Any]],
        invoke_exception: Exception | None,
        **kwargs: Any,
    ) -> None:
        """
        Called after agent invocation.

        Stores successful problem-solution pairs.

        Args:
            request_messages: The original request messages.
            response_messages: The response messages.
            invoke_exception: Exception if invocation failed.
            **kwargs: Additional context.
        """
        if invoke_exception:
            return

        # Only cache successful diagnostic/troubleshooting resolutions
        workflow_id = kwargs.get("workflow_id", "")
        if workflow_id not in {"wf1"}:  # Only cache diagnostics for now
            return

        # Check if resolution was marked as successful
        resolution_successful = kwargs.get("resolution_successful", False)
        if not resolution_successful:
            return

        # Extract problem from user messages
        user_messages = [m for m in request_messages if m.get("role") == "user"]
        if not user_messages:
            return

        problem = user_messages[-1].get("content", "")
        if isinstance(problem, list):
            problem = " ".join(p.get("text", "") for p in problem if p.get("type") == "text")

        # Extract solution from assistant response
        assistant_messages = [m for m in response_messages if m.get("role") == "assistant"]
        if not assistant_messages:
            return

        solution = assistant_messages[-1].get("content", "")
        if isinstance(solution, list):
            solution = " ".join(p.get("text", "") for p in solution if p.get("type") == "text")

        # Add to cache
        if problem and solution:
            await self.add_solution(
                problem=problem,
                solution=solution,
                metadata={
                    "workflow_id": workflow_id,
                    "thread_id": kwargs.get("thread_id"),
                    "user_id": kwargs.get("user_id"),
                },
            )

        logger.debug(
            "ProblemSolutionProvider.invoked - solution cached",
            workflow_id=workflow_id,
            problem_preview=problem[:50],
        )


# Module-level singleton
_provider: ProblemSolutionProvider | None = None


def get_problem_solution_provider() -> ProblemSolutionProvider:
    """Get the singleton problem-solution provider."""
    global _provider
    if _provider is None:
        _provider = ProblemSolutionProvider()
    return _provider
