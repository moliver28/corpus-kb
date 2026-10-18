"""Error handling — structured error responses for handlers.

Wraps command/query handler methods with try/except and returns
structured error dicts. Includes timeout and retry logic for
transient database failures.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar, cast

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Exceptions that should NOT be retried (logic errors, not transient)
_NO_RETRY = (ValueError, KeyError, FileNotFoundError, TypeError)


def handle_errors(
    timeout_seconds: float = 30.0,
    max_retries: int = 3,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., T | dict[str, str]]]:
    """Decorator: wrap handler method with error handling, timeout, retry.

    Args:
        timeout_seconds: Max time per attempt.
        max_retries: Number of retries for transient failures.

    Returns:
        Decorated function that returns error dict on failure.
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., T | dict[str, str]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T | dict[str, str]:
            last_error: Exception | None = None
            for attempt in range(max_retries):
                try:
                    result = await asyncio.wait_for(
                        func(*args, **kwargs),
                        timeout=timeout_seconds,
                    )
                    return result
                except _NO_RETRY as exc:
                    # Logic error — don't retry
                    logger.error(
                        "%s failed (attempt %d): %s: %s",
                        func.__name__,
                        attempt + 1,
                        type(exc).__name__,
                        exc,
                    )
                    return _error_dict(exc)
                except TimeoutError as exc:
                    last_error = exc
                    logger.warning(
                        "%s timed out (attempt %d/%d)",
                        func.__name__,
                        attempt + 1,
                        max_retries,
                    )
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "%s failed (attempt %d/%d): %s: %s",
                        func.__name__,
                        attempt + 1,
                        max_retries,
                        type(exc).__name__,
                        exc,
                    )
                    # Exponential backoff before retry
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2**attempt)

            # All retries exhausted
            logger.error(
                "%s failed after %d retries: %s",
                func.__name__,
                max_retries,
                last_error,
            )
            return _error_dict(last_error or RuntimeError("Unknown error"))

        # functools.wraps types the wrapper as the wrapped callable; widen it
        # to the decorator's honest contract (result or error dict).
        return cast("Callable[..., T | dict[str, str]]", wrapper)

    return decorator


def _error_dict(exc: Exception) -> dict[str, str]:
    """Build a structured error response dict."""
    return {
        "status": "error",
        "error": str(exc),
        "error_type": type(exc).__name__,
    }
