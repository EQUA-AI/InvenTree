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

from ai.core.middleware.reflection import (
    ErrorCategory,
    ExecutionContext,
    ToolExecutionResult,
    ReflectionFunctionMiddleware,
    LoggingMiddleware,
    MetricsMiddleware,
    MetricsSummary,
    get_reflection_middleware,
    get_logging_middleware,
    get_metrics_middleware,
)

from ai.core.middleware.retry import (
    RetryConfig,
    RetryStats,
    RetryContext,
    is_retryable_error,
    retry_async,
    with_retry,
    retry_azure_openai_call,
    retry_with_fallback,
    get_retry_stats,
    AZURE_OPENAI_RETRY_CONFIG,
)

from ai.core.middleware.rate_limit import (
    RateLimitConfig,
    RateLimiter,
    RateLimitResult,
    RateLimitStats,
    RateLimitMiddleware,
    rate_limit,
    get_rate_limiter,
)

__all__ = [
    # Error handling
    "ErrorCategory",
    "ExecutionContext",
    "ToolExecutionResult",
    # Middleware classes
    "ReflectionFunctionMiddleware",
    "LoggingMiddleware",
    "MetricsMiddleware",
    "MetricsSummary",
    # Factory functions
    "get_reflection_middleware",
    "get_logging_middleware",
    "get_metrics_middleware",
    # Retry utilities
    "RetryConfig",
    "RetryStats",
    "RetryContext",
    "is_retryable_error",
    "retry_async",
    "with_retry",
    "retry_azure_openai_call",
    "retry_with_fallback",
    "get_retry_stats",
    "AZURE_OPENAI_RETRY_CONFIG",
    # Rate limiting
    "RateLimitConfig",
    "RateLimiter",
    "RateLimitResult",
    "RateLimitStats",
    "RateLimitMiddleware",
    "rate_limit",
    "get_rate_limiter",
]
