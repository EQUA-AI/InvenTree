"""
AIMMS Middleware Module

Contains middleware components for cross-cutting concerns:
- ReflectionFunctionMiddleware: LLM-based error reflection and recovery
- LoggingMiddleware: Structured logging with correlation IDs
- MetricsMiddleware: Performance and usage metrics
- RetryMiddleware: Exponential backoff retry for transient failures
- RateLimitMiddleware: Token bucket rate limiting
- Error taxonomy and execution context
"""

from ai.core.middleware.rate_limit import (
    RateLimitConfig,
    RateLimiter,
    RateLimitMiddleware,
    RateLimitResult,
    RateLimitStats,
    WindowedRateLimiter,
    get_rate_limiter,
    get_windowed_rate_limiter,
    rate_limit,
)
from ai.core.middleware.reflection import (
    ErrorCategory,
    ExecutionContext,
    LoggingMiddleware,
    MetricsMiddleware,
    MetricsSummary,
    ReflectionFunctionMiddleware,
    ToolExecutionResult,
    get_logging_middleware,
    get_metrics_middleware,
    get_reflection_middleware,
)
from ai.core.middleware.retry import (
    AZURE_OPENAI_RETRY_CONFIG,
    RetryConfig,
    RetryStats,
    get_retry_stats,
    is_retryable_error,
    retry_async,
    retry_azure_openai_call,
    retry_with_fallback,
    with_retry,
)

__all__ = [
    "AZURE_OPENAI_RETRY_CONFIG",
    "ErrorCategory",
    "ExecutionContext",
    "LoggingMiddleware",
    "MetricsMiddleware",
    "MetricsSummary",
    "RateLimitConfig",
    "RateLimitMiddleware",
    "RateLimitResult",
    "RateLimitStats",
    "RateLimiter",
    "ReflectionFunctionMiddleware",
    "RetryConfig",
    "RetryStats",
    "ToolExecutionResult",
    "WindowedRateLimiter",
    "get_logging_middleware",
    "get_metrics_middleware",
    "get_rate_limiter",
    "get_reflection_middleware",
    "get_retry_stats",
    "get_windowed_rate_limiter",
    "is_retryable_error",
    "rate_limit",
    "retry_async",
    "retry_azure_openai_call",
    "retry_with_fallback",
    "with_retry",
]
