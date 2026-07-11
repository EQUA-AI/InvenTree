"""
Retry Logic and Resilience Utilities

Provides exponential backoff retry mechanisms for handling transient failures
when calling Azure OpenAI and other external services.

Features:
- Exponential backoff with jitter
- Configurable retry counts and delays
- Error classification (retryable vs fatal)
- Async context manager for retry blocks
- Decorator for easy function wrapping

Usage:
    # As decorator
    @with_retry(max_attempts=3, base_delay=1.0)
    async def call_azure_openai():
        ...
    
    # As context manager
    async with RetryContext(max_attempts=3) as ctx:
        result = await risky_operation()
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ===== Retryable Error Classification =====

# HTTP status codes that should trigger retry
RETRYABLE_STATUS_CODES = {
    429,  # Too Many Requests (rate limit)
    500,  # Internal Server Error
    502,  # Bad Gateway
    503,  # Service Unavailable
    504,  # Gateway Timeout
}

# Exception types that should trigger retry
RETRYABLE_EXCEPTIONS = (
    asyncio.TimeoutError,
    ConnectionError,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
)


def is_retryable_error(error: Exception) -> bool:
    """
    Determine if an error is retryable.
    
    Args:
        error: The exception to classify
        
    Returns:
        True if the error is transient and worth retrying
    """
    # Check exception types
    if isinstance(error, RETRYABLE_EXCEPTIONS):
        return True
    
    # Check HTTP status codes from httpx responses
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in RETRYABLE_STATUS_CODES
    
    # Check for Azure OpenAI specific errors
    error_message = str(error).lower()
    if any(keyword in error_message for keyword in [
        "rate limit",
        "throttl",
        "capacity",
        "overloaded",
        "timeout",
        "temporarily unavailable",
        "service unavailable",
        "connection reset",
    ]):
        return True
    
    return False


def extract_retry_after(error: Exception) -> float | None:
    """
    Extract Retry-After header from an error response.
    
    Azure OpenAI often includes this header when rate limiting.
    
    Returns:
        Seconds to wait, or None if not available
    """
    if isinstance(error, httpx.HTTPStatusError):
        retry_after = error.response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
    return None


# ===== Retry Configuration =====

@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    
    max_attempts: int = 3
    base_delay: float = 1.0  # seconds
    max_delay: float = 30.0  # seconds
    exponential_base: float = 2.0
    jitter: bool = True  # Add randomness to prevent thundering herd
    jitter_factor: float = 0.1  # ±10% of delay
    
    def calculate_delay(self, attempt: int, retry_after: float | None = None) -> float:
        """
        Calculate delay before next retry.
        
        Uses exponential backoff with optional jitter:
        delay = min(base_delay * (exponential_base ^ attempt), max_delay)
        
        If Retry-After header is provided, respects that instead.
        """
        if retry_after is not None:
            return min(retry_after, self.max_delay)
        
        delay = self.base_delay * (self.exponential_base ** attempt)
        delay = min(delay, self.max_delay)
        
        if self.jitter:
            jitter_range = delay * self.jitter_factor
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0.1, delay)  # Minimum 100ms delay


# Default configuration for Azure OpenAI calls
AZURE_OPENAI_RETRY_CONFIG = RetryConfig(
    max_attempts=5,
    base_delay=1.0,
    max_delay=60.0,
    exponential_base=2.0,
    jitter=True,
)


# ===== Retry Statistics =====

@dataclass
class RetryStats:
    """Statistics about retry attempts."""
    
    total_attempts: int = 0
    successful_attempts: int = 0
    failed_attempts: int = 0
    retries_performed: int = 0
    total_retry_delay: float = 0.0
    last_error: str | None = None
    errors_by_type: dict[str, int] = field(default_factory=dict)
    
    def record_attempt(self, success: bool, retries: int, delay: float, error: Exception | None = None) -> None:
        """Record the outcome of an operation."""
        self.total_attempts += 1
        self.retries_performed += retries
        self.total_retry_delay += delay
        
        if success:
            self.successful_attempts += 1
        else:
            self.failed_attempts += 1
            if error:
                self.last_error = str(error)
                error_type = type(error).__name__
                self.errors_by_type[error_type] = self.errors_by_type.get(error_type, 0) + 1
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_attempts": self.total_attempts,
            "successful_attempts": self.successful_attempts,
            "failed_attempts": self.failed_attempts,
            "retries_performed": self.retries_performed,
            "total_retry_delay_seconds": round(self.total_retry_delay, 2),
            "success_rate": round(self.successful_attempts / max(1, self.total_attempts), 3),
            "avg_retries_per_attempt": round(self.retries_performed / max(1, self.total_attempts), 2),
            "errors_by_type": self.errors_by_type,
            "last_error": self.last_error,
        }


# Global stats instance
_retry_stats = RetryStats()


def get_retry_stats() -> RetryStats:
    """Get global retry statistics."""
    return _retry_stats


# ===== Retry Implementation =====

async def retry_async(
    func: Callable[[], Coroutine[Any, Any, T]],
    config: RetryConfig | None = None,
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> T:
    """
    Execute an async function with retry logic.
    
    Args:
        func: Async callable to execute
        config: Retry configuration (uses Azure OpenAI defaults if None)
        on_retry: Optional callback when retry occurs (attempt, error, delay)
        
    Returns:
        Result of the function call
        
    Raises:
        Last exception if all retries fail
    """
    config = config or AZURE_OPENAI_RETRY_CONFIG
    last_error: Exception | None = None
    total_delay = 0.0
    
    for attempt in range(config.max_attempts):
        try:
            result = await func()
            _retry_stats.record_attempt(True, attempt, total_delay)
            return result
            
        except Exception as e:
            last_error = e
            
            # Check if error is retryable
            if not is_retryable_error(e):
                logger.warning(f"Non-retryable error on attempt {attempt + 1}: {type(e).__name__}: {e}")
                _retry_stats.record_attempt(False, attempt, total_delay, e)
                raise
            
            # Check if we have retries left
            if attempt + 1 >= config.max_attempts:
                logger.error(f"All {config.max_attempts} retry attempts exhausted: {e}")
                _retry_stats.record_attempt(False, attempt + 1, total_delay, e)
                raise
            
            # Calculate delay
            retry_after = extract_retry_after(e)
            delay = config.calculate_delay(attempt, retry_after)
            total_delay += delay
            
            logger.warning(
                f"Retryable error on attempt {attempt + 1}/{config.max_attempts}: "
                f"{type(e).__name__}: {e}. Retrying in {delay:.2f}s"
            )
            
            # Call retry callback if provided
            if on_retry:
                on_retry(attempt + 1, e, delay)
            
            # Wait before retry
            await asyncio.sleep(delay)
    
    # Should not reach here, but just in case
    _retry_stats.record_attempt(False, config.max_attempts, total_delay, last_error)
    raise last_error or RuntimeError("Retry loop completed without result")


def with_retry(
    config: RetryConfig | None = None,
    **config_kwargs: Any,
) -> Callable[[Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]]:
    """
    Decorator to add retry logic to async functions.
    
    Usage:
        @with_retry(max_attempts=3, base_delay=1.0)
        async def call_azure():
            ...
        
        @with_retry()  # Uses defaults
        async def call_azure():
            ...
    """
    if config is None and config_kwargs:
        config = RetryConfig(**config_kwargs)
    elif config is None:
        config = AZURE_OPENAI_RETRY_CONFIG
    
    def decorator(func: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., Coroutine[Any, Any, T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await retry_async(
                lambda: func(*args, **kwargs),
                config=config,
            )
        return wrapper
    
    return decorator


class RetryContext:
    """
    Async context manager for retry blocks.
    
    Usage:
        async with RetryContext(max_attempts=3) as ctx:
            result = await risky_operation()
            
        print(f"Succeeded after {ctx.attempts} attempts")
    """
    
    def __init__(
        self,
        config: RetryConfig | None = None,
        **config_kwargs: Any,
    ):
        """Initialize retry context."""
        if config is None and config_kwargs:
            self.config = RetryConfig(**config_kwargs)
        elif config is None:
            self.config = AZURE_OPENAI_RETRY_CONFIG
        else:
            self.config = config
        
        self.attempts = 0
        self.total_delay = 0.0
        self.last_error: Exception | None = None
    
    async def __aenter__(self) -> "RetryContext":
        """Enter context."""
        return self
    
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """
        Handle exceptions and decide whether to retry.
        
        Returns True to suppress the exception (retry), False to propagate.
        """
        if exc_val is None:
            # No exception, success
            _retry_stats.record_attempt(True, self.attempts, self.total_delay)
            return False
        
        self.last_error = exc_val
        
        # Check if retryable
        if not is_retryable_error(exc_val):
            _retry_stats.record_attempt(False, self.attempts, self.total_delay, exc_val)
            return False  # Propagate non-retryable errors
        
        # Check if retries remain
        if self.attempts + 1 >= self.config.max_attempts:
            _retry_stats.record_attempt(False, self.attempts + 1, self.total_delay, exc_val)
            return False  # Exhausted retries
        
        # Calculate delay
        retry_after = extract_retry_after(exc_val)
        delay = self.config.calculate_delay(self.attempts, retry_after)
        self.total_delay += delay
        self.attempts += 1
        
        logger.warning(
            f"Retry context: attempt {self.attempts}/{self.config.max_attempts} "
            f"after {type(exc_val).__name__}: {exc_val}. Waiting {delay:.2f}s"
        )
        
        await asyncio.sleep(delay)
        return True  # Suppress exception, will retry


# ===== Specialized Retry Functions =====

async def retry_azure_openai_call(
    func: Callable[[], Coroutine[Any, Any, T]],
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> T:
    """
    Retry wrapper specifically for Azure OpenAI calls.
    
    Uses configuration optimized for Azure OpenAI rate limits:
    - 5 max attempts
    - 1-60 second delays
    - Respects Retry-After headers
    - Exponential backoff with jitter
    """
    return await retry_async(func, config=AZURE_OPENAI_RETRY_CONFIG, on_retry=on_retry)


async def retry_with_fallback(
    primary: Callable[[], Coroutine[Any, Any, T]],
    fallback: Callable[[], Coroutine[Any, Any, T]],
    primary_config: RetryConfig | None = None,
) -> tuple[T, bool]:
    """
    Try primary function with retries, fall back if exhausted.
    
    Args:
        primary: Primary async function to try
        fallback: Fallback function if primary fails
        primary_config: Retry config for primary (uses defaults if None)
        
    Returns:
        Tuple of (result, used_fallback)
    """
    try:
        result = await retry_async(primary, config=primary_config)
        return result, False
    except Exception as e:
        logger.warning(f"Primary function failed after retries: {e}. Using fallback.")
        result = await fallback()
        return result, True


# Export all symbols
__all__ = [
    # Error classification
    "is_retryable_error",
    "extract_retry_after",
    "RETRYABLE_STATUS_CODES",
    "RETRYABLE_EXCEPTIONS",
    # Configuration
    "RetryConfig",
    "AZURE_OPENAI_RETRY_CONFIG",
    # Statistics
    "RetryStats",
    "get_retry_stats",
    # Core retry functions
    "retry_async",
    "with_retry",
    "RetryContext",
    # Specialized functions
    "retry_azure_openai_call",
    "retry_with_fallback",
]
