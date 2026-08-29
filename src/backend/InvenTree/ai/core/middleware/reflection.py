"""
AIMMS Middleware Module

Provides middleware components for agent execution:
- ReflectionFunctionMiddleware: LLM-based error reflection and recovery
- LoggingMiddleware: Structured logging with correlation IDs
- MetricsMiddleware: Performance and usage metrics
- ValidationMiddleware: Input/output validation
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar

from agent_framework import ChatAgent
from agent_framework.azure import AzureOpenAIChatClient
from ai.core.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ErrorCategory(Enum):
    """Categories of errors for handling strategies."""

    TRANSIENT_INFRA = "transient_infra"  # Retry automatically
    VALIDATION = "validation"  # LLM reflection
    BUSINESS_RULE = "business_rule"  # Surface to user
    TOOL_EXECUTION = "tool_execution"  # May retry with modified params
    UNKNOWN = "unknown"  # Log and escalate


def _turn_correlation() -> str:
    """The active turn's bound correlation id, or '' outside a turn (S36).

    Consuming the turn's id instead of minting per call is what lets a tool
    log line join the same spine as the utterance and the proposal.
    """
    from ai.core.correlation import current_correlation

    return current_correlation()


@dataclass
class ExecutionContext:
    """Context for middleware execution."""

    correlation_id: str = field(default_factory=lambda: _turn_correlation() or str(uuid.uuid4()))
    thread_id: str = ""
    user_id: str = ""
    workflow_id: str = ""
    agent_name: str = ""
    tool_name: str = ""
    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def elapsed_ms(self) -> float:
        """Get elapsed time in milliseconds."""
        return (datetime.now(UTC) - self.start_time).total_seconds() * 1000


@dataclass
class ToolExecutionResult:
    """Result of a tool execution with middleware."""

    success: bool
    result: Any = None
    error: str | None = None
    error_category: ErrorCategory = ErrorCategory.UNKNOWN
    retries: int = 0
    reflected: bool = False
    reflection_suggestion: str | None = None
    execution_time_ms: float = 0.0


class ReflectionFunctionMiddleware:
    """
    Middleware that uses LLM reflection to handle tool execution errors.

    When a tool fails, this middleware:
    1. Categorizes the error
    2. For validation errors, asks LLM to reflect and suggest corrections
    3. May retry with suggested corrections
    4. For business rule errors, surfaces to user with explanation

    Based on MAF error taxonomy:
    - TRANSIENT_INFRA: Auto-retry with backoff
    - VALIDATION: LLM reflection for correction
    - BUSINESS_RULE: Surface to user
    """

    REFLECTION_PROMPT = """You are an error analysis specialist.
A tool execution failed with the following error:

Tool: {tool_name}
Arguments: {arguments}
Error: {error}

Analyze this error and provide:
1. ERROR CATEGORY: One of [TRANSIENT_INFRA, VALIDATION, BUSINESS_RULE]
   - TRANSIENT_INFRA: Network issues, timeouts, service unavailable
   - VALIDATION: Invalid parameters, missing required fields, format errors
   - BUSINESS_RULE: The operation is not allowed by business logic

2. ROOT CAUSE: Brief explanation of why this happened

3. SUGGESTION: How to fix or work around this error

4. CORRECTED_ARGUMENTS: If category is VALIDATION, provide corrected arguments as JSON

Respond in this format:
CATEGORY: [category]
ROOT_CAUSE: [explanation]
SUGGESTION: [how to fix]
CORRECTED_ARGUMENTS: [JSON or "N/A"]"""

    def __init__(
        self,
        max_retries: int = 3,
        reflection_enabled: bool = True,
    ):
        """
        Initialize middleware.

        Args:
            max_retries: Maximum retry attempts for transient errors
            reflection_enabled: Whether to use LLM reflection
        """
        self.max_retries = max_retries
        self.reflection_enabled = reflection_enabled
        self._reflection_agent: ChatAgent | None = None

    async def _get_reflection_agent(self) -> ChatAgent:
        """Get or create reflection agent."""
        if self._reflection_agent is None:
            settings = get_settings()

            chat_client = AzureOpenAIChatClient(
                deployment_name=settings.azure_openai_deployment,
                endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
            )

            self._reflection_agent = ChatAgent(
                chat_client=chat_client,
                instructions="You are an error analysis specialist. Always respond in the specified format.",
                name="Reflection Agent",
            )

        return self._reflection_agent

    def categorize_error(self, error: Exception) -> ErrorCategory:
        """
        Categorize error based on type and message.

        This provides fast categorization without LLM call.
        LLM reflection is used for deeper analysis when needed.
        """
        error_str = str(error).lower()
        error_type = type(error).__name__.lower()

        # Transient infrastructure errors
        transient_keywords = [
            "timeout",
            "connection",
            "network",
            "unavailable",
            "retry",
            "rate limit",
            "429",
            "503",
            "504",
            "circuit",
            "breaker",
        ]
        if any(kw in error_str or kw in error_type for kw in transient_keywords):
            return ErrorCategory.TRANSIENT_INFRA

        # Validation errors
        validation_keywords = [
            "invalid",
            "validation",
            "required",
            "missing",
            "format",
            "type error",
            "value error",
            "parse",
            "400",
            "422",
        ]
        if any(kw in error_str or kw in error_type for kw in validation_keywords):
            return ErrorCategory.VALIDATION

        # Business rule errors
        business_keywords = [
            "permission",
            "forbidden",
            "not allowed",
            "policy",
            "business",
            "rule",
            "constraint",
            "403",
            "insufficient",
            "quota",
            "limit exceeded",
        ]
        if any(kw in error_str or kw in error_type for kw in business_keywords):
            return ErrorCategory.BUSINESS_RULE

        return ErrorCategory.UNKNOWN

    async def reflect_on_error(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        error: Exception,
    ) -> dict[str, Any]:
        """
        Use LLM to reflect on error and suggest correction.

        Returns:
            Dict with category, root_cause, suggestion, corrected_arguments
        """
        if not self.reflection_enabled:
            return {
                "category": self.categorize_error(error),
                "root_cause": str(error),
                "suggestion": "Unable to provide suggestion (reflection disabled)",
                "corrected_arguments": None,
            }

        try:
            agent = await self._get_reflection_agent()

            prompt = self.REFLECTION_PROMPT.format(
                tool_name=tool_name,
                arguments=str(arguments),
                error=str(error),
            )

            response = await agent.run(prompt)
            # S12 (WP-B2): reflection repair is a real provider call the
            # turn ledger was blind to.
            from ai.core.usage import maf_response_usage_metrics, record_usage

            record_usage("reflection_repair", maf_response_usage_metrics(response))
            response_text = ""
            if response.messages:
                last_msg = response.messages[-1]
                response_text = last_msg.text if hasattr(last_msg, "text") else str(last_msg)

            # Parse response
            return self._parse_reflection_response(response_text)

        except Exception as e:
            logger.warning(f"Reflection failed: {e}")
            return {
                "category": self.categorize_error(error),
                "root_cause": str(error),
                "suggestion": "Reflection failed, using basic categorization",
                "corrected_arguments": None,
            }

    def _parse_reflection_response(self, response: str) -> dict[str, Any]:
        """Parse structured reflection response."""
        result = {
            "category": ErrorCategory.UNKNOWN,
            "root_cause": "",
            "suggestion": "",
            "corrected_arguments": None,
        }

        lines = response.split("\n")
        for line in lines:
            line = line.strip()

            if line.startswith("CATEGORY:"):
                cat_str = line.replace("CATEGORY:", "").strip().upper()
                with contextlib.suppress(KeyError):
                    result["category"] = ErrorCategory[cat_str]

            elif line.startswith("ROOT_CAUSE:"):
                result["root_cause"] = line.replace("ROOT_CAUSE:", "").strip()

            elif line.startswith("SUGGESTION:"):
                result["suggestion"] = line.replace("SUGGESTION:", "").strip()

            elif line.startswith("CORRECTED_ARGUMENTS:"):
                args_str = line.replace("CORRECTED_ARGUMENTS:", "").strip()
                if args_str.lower() != "n/a":
                    try:
                        import json

                        result["corrected_arguments"] = json.loads(args_str)
                    except json.JSONDecodeError:
                        pass

        return result

    async def execute_with_reflection(
        self,
        func: Callable[..., T],
        *args,
        tool_name: str = "",
        context: ExecutionContext | None = None,
        **kwargs,
    ) -> ToolExecutionResult:
        """
        Execute a function with reflection-based error handling.

        Args:
            func: The function to execute
            args: Positional arguments
            tool_name: Name of the tool for logging
            context: Execution context
            kwargs: Keyword arguments

        Returns:
            ToolExecutionResult with outcome and any reflections
        """
        ctx = context or ExecutionContext()
        ctx.tool_name = tool_name or func.__name__

        retries = 0
        last_error: Exception | None = None

        while retries <= self.max_retries:
            try:
                start = time.perf_counter()

                # Execute the function
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)

                execution_time = (time.perf_counter() - start) * 1000

                return ToolExecutionResult(
                    success=True,
                    result=result,
                    retries=retries,
                    execution_time_ms=execution_time,
                )

            except Exception as e:
                last_error = e
                category = self.categorize_error(e)

                logger.warning(
                    f"Tool execution failed: {e}",
                    extra={
                        "tool_name": ctx.tool_name,
                        "correlation_id": ctx.correlation_id,
                        "category": category.value,
                        "retry": retries,
                    },
                )

                # Handle based on category
                if category == ErrorCategory.TRANSIENT_INFRA:
                    # Retry with exponential backoff
                    if retries < self.max_retries:
                        await asyncio.sleep(2**retries)
                        retries += 1
                        continue

                elif category == ErrorCategory.VALIDATION:
                    # Reflect and try to correct
                    if self.reflection_enabled and retries < self.max_retries:
                        reflection = await self.reflect_on_error(
                            ctx.tool_name,
                            kwargs,
                            e,
                        )

                        if reflection["corrected_arguments"]:
                            # Retry with corrected arguments
                            kwargs.update(reflection["corrected_arguments"])
                            retries += 1
                            continue
                        else:
                            # Return with suggestion
                            return ToolExecutionResult(
                                success=False,
                                error=str(e),
                                error_category=category,
                                retries=retries,
                                reflected=True,
                                reflection_suggestion=reflection["suggestion"],
                                execution_time_ms=ctx.elapsed_ms(),
                            )

                elif category == ErrorCategory.BUSINESS_RULE:
                    # Don't retry, surface to user
                    return ToolExecutionResult(
                        success=False,
                        error=str(e),
                        error_category=category,
                        retries=retries,
                        execution_time_ms=ctx.elapsed_ms(),
                    )

                # Unknown error - break and return
                break

        # All retries exhausted
        return ToolExecutionResult(
            success=False,
            error=str(last_error) if last_error else "Unknown error",
            error_category=ErrorCategory.UNKNOWN,
            retries=retries,
            execution_time_ms=ctx.elapsed_ms(),
        )


class LoggingMiddleware:
    """
    Middleware for structured logging with correlation IDs.

    Logs:
    - Tool invocations with parameters
    - Execution times
    - Errors with context
    - Agent handoffs
    """

    def __init__(self, logger_name: str = "aimms"):
        self.logger = logging.getLogger(logger_name)

    def wrap(
        self,
        func: Callable[..., T],
        tool_name: str = "",
    ) -> Callable[..., T]:
        """
        Wrap a function with logging.

        Usage:
            @logging_middleware.wrap(tool_name="search_parts")
            async def search_parts(...):
                ...
        """

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            correlation_id = (
                kwargs.pop("_correlation_id", None) or _turn_correlation() or str(uuid.uuid4())
            )
            start = time.perf_counter()

            self.logger.info(
                f"Tool invocation: {tool_name or func.__name__}",
                extra={
                    "correlation_id": correlation_id,
                    "tool_name": tool_name or func.__name__,
                    "event": "tool_start",
                },
            )

            try:
                result = func(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result

                elapsed = (time.perf_counter() - start) * 1000
                self.logger.info(
                    f"Tool completed: {tool_name or func.__name__}",
                    extra={
                        "correlation_id": correlation_id,
                        "tool_name": tool_name or func.__name__,
                        "event": "tool_complete",
                        "execution_time_ms": elapsed,
                    },
                )

                return result

            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                self.logger.error(
                    f"Tool failed: {tool_name or func.__name__} - {e}",
                    extra={
                        "correlation_id": correlation_id,
                        "tool_name": tool_name or func.__name__,
                        "event": "tool_error",
                        "error": str(e),
                        "execution_time_ms": elapsed,
                    },
                )
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            correlation_id = (
                kwargs.pop("_correlation_id", None) or _turn_correlation() or str(uuid.uuid4())
            )
            start = time.perf_counter()

            self.logger.info(
                f"Tool invocation: {tool_name or func.__name__}",
                extra={
                    "correlation_id": correlation_id,
                    "tool_name": tool_name or func.__name__,
                    "event": "tool_start",
                },
            )

            try:
                result = func(*args, **kwargs)

                elapsed = (time.perf_counter() - start) * 1000
                self.logger.info(
                    f"Tool completed: {tool_name or func.__name__}",
                    extra={
                        "correlation_id": correlation_id,
                        "tool_name": tool_name or func.__name__,
                        "event": "tool_complete",
                        "execution_time_ms": elapsed,
                    },
                )

                return result

            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                self.logger.error(
                    f"Tool failed: {tool_name or func.__name__} - {e}",
                    extra={
                        "correlation_id": correlation_id,
                        "tool_name": tool_name or func.__name__,
                        "event": "tool_error",
                        "error": str(e),
                        "execution_time_ms": elapsed,
                    },
                )
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper


@dataclass
class MetricsSummary:
    """Summary of execution metrics."""

    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    total_retries: int = 0
    avg_execution_time_ms: float = 0.0
    p95_execution_time_ms: float = 0.0
    error_rate: float = 0.0
    errors_by_category: dict[str, int] = field(default_factory=dict)


class MetricsMiddleware:
    """
    Middleware for collecting execution metrics.

    Tracks:
    - Execution counts
    - Success/failure rates
    - Execution times
    - Error categories
    """

    def __init__(self):
        self._executions: list[dict[str, Any]] = []
        self._max_history = 1000

    def record_execution(
        self,
        tool_name: str,
        success: bool,
        execution_time_ms: float,
        error_category: ErrorCategory | None = None,
        retries: int = 0,
    ) -> None:
        """Record an execution."""
        self._executions.append({
            "tool_name": tool_name,
            "success": success,
            "execution_time_ms": execution_time_ms,
            "error_category": error_category.value if error_category else None,
            "retries": retries,
            "timestamp": datetime.now(UTC).isoformat(),
        })

        # Trim history
        if len(self._executions) > self._max_history:
            self._executions = self._executions[-self._max_history :]

    def get_summary(self, tool_name: str | None = None) -> MetricsSummary:
        """Get metrics summary, optionally filtered by tool."""
        executions = self._executions
        if tool_name:
            executions = [e for e in executions if e["tool_name"] == tool_name]

        if not executions:
            return MetricsSummary()

        successful = [e for e in executions if e["success"]]
        failed = [e for e in executions if not e["success"]]
        times = [e["execution_time_ms"] for e in executions]

        # Calculate p95
        sorted_times = sorted(times)
        p95_idx = int(len(sorted_times) * 0.95)
        p95 = sorted_times[p95_idx] if sorted_times else 0.0

        # Count errors by category
        errors_by_cat = {}
        for e in failed:
            cat = e.get("error_category", "unknown")
            errors_by_cat[cat] = errors_by_cat.get(cat, 0) + 1

        return MetricsSummary(
            total_executions=len(executions),
            successful_executions=len(successful),
            failed_executions=len(failed),
            total_retries=sum(e.get("retries", 0) for e in executions),
            avg_execution_time_ms=sum(times) / len(times) if times else 0.0,
            p95_execution_time_ms=p95,
            error_rate=len(failed) / len(executions) if executions else 0.0,
            errors_by_category=errors_by_cat,
        )


# Global middleware instances
_reflection_middleware: ReflectionFunctionMiddleware | None = None
_logging_middleware: LoggingMiddleware | None = None
_metrics_middleware: MetricsMiddleware | None = None


def get_reflection_middleware() -> ReflectionFunctionMiddleware:
    """Get shared reflection middleware instance."""
    global _reflection_middleware
    if _reflection_middleware is None:
        _reflection_middleware = ReflectionFunctionMiddleware()
    return _reflection_middleware


def get_logging_middleware() -> LoggingMiddleware:
    """Get shared logging middleware instance."""
    global _logging_middleware
    if _logging_middleware is None:
        _logging_middleware = LoggingMiddleware()
    return _logging_middleware


def get_metrics_middleware() -> MetricsMiddleware:
    """Get shared metrics middleware instance."""
    global _metrics_middleware
    if _metrics_middleware is None:
        _metrics_middleware = MetricsMiddleware()
    return _metrics_middleware
