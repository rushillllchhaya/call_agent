"""
Async retry decorator with exponential backoff — inspired by AVA's error handling.
===================================================================================
Provides a reusable decorator for any async function that may fail transiently
(rate limits, network timeouts, API errors). Used across STT, LLM, TTS, and
HTTP API calls in the BSB voice pipeline.

Usage:
    @with_retry(max_retries=3, retry_on=(RateLimitError, TimeoutError))
    async def call_groq(...):
        ...
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from functools import wraps
from typing import Any, Callable, Optional, Sequence, Type, Union

logger = logging.getLogger("retry")


class RetryExhausted(Exception):
    """All retry attempts failed."""
    def __init__(self, attempts: int, last_error: Exception):
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"All {attempts} attempts failed. Last error: {last_error}")


def with_retry(
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    backoff_factor: float = 2.0,
    jitter: float = 0.3,
    retry_on: Union[Type[Exception], Sequence[Type[Exception]]] = (Exception,),
    fallback: Optional[Any] = None,
    fallback_factory: Optional[Callable] = None,
    log_name: Optional[str] = None,
):
    """
    Async retry decorator with exponential backoff + jitter.

    Args:
        max_retries:     Maximum number of retry attempts (0 = no retries, just run once)
        base_delay:      Initial delay between retries (seconds)
        max_delay:       Maximum delay cap (seconds)
        backoff_factor:  Multiply delay by this factor on each retry
        jitter:          Random jitter as fraction of delay (0.3 = ±30%)
        retry_on:        Exception types to retry on (others propagate immediately)
        fallback:        Static value to return if all retries fail (instead of raising)
        fallback_factory: Callable to produce fallback value (takes no args)
        log_name:        Name for log messages (defaults to function name)
    """
    if isinstance(retry_on, type):
        retry_on = (retry_on,)

    def decorator(func: Callable) -> Callable:
        fname = log_name or func.__name__

        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None
            delay = base_delay

            for attempt in range(1, max_retries + 2):  # +2 because range is exclusive and attempt 1 is first try
                try:
                    return await func(*args, **kwargs)
                except tuple(retry_on) as e:
                    last_error = e
                    if attempt > max_retries:
                        break

                    # Calculate delay with jitter
                    jittered = delay * (1 + random.uniform(-jitter, jitter))
                    jittered = min(jittered, max_delay)

                    error_type = type(e).__name__
                    logger.warning(
                        f"[Retry] {fname} attempt {attempt}/{max_retries + 1} failed "
                        f"({error_type}: {str(e)[:100]}). "
                        f"Retrying in {jittered:.1f}s..."
                    )

                    await asyncio.sleep(jittered)
                    delay = min(delay * backoff_factor, max_delay)

                except Exception:
                    # Non-retryable exception — propagate immediately
                    raise

            # All retries exhausted
            logger.error(
                f"[Retry] {fname} EXHAUSTED after {max_retries + 1} attempts. "
                f"Last error: {type(last_error).__name__}: {str(last_error)[:200]}"
            )

            if fallback_factory is not None:
                return fallback_factory()
            if fallback is not None:
                return fallback
            raise RetryExhausted(max_retries + 1, last_error)

        return wrapper
    return decorator


async def retry_call(
    func: Callable,
    *args,
    max_retries: int = 2,
    base_delay: float = 0.5,
    retry_on: Union[Type[Exception], Sequence[Type[Exception]]] = (Exception,),
    fallback: Optional[Any] = None,
    **kwargs,
) -> Any:
    """
    Functional (non-decorator) retry — for one-off calls.

    Usage:
        result = await retry_call(some_async_func, arg1, arg2, max_retries=3)
    """
    if isinstance(retry_on, type):
        retry_on = (retry_on,)

    last_error = None
    delay = base_delay

    for attempt in range(1, max_retries + 2):
        try:
            return await func(*args, **kwargs)
        except tuple(retry_on) as e:
            last_error = e
            if attempt > max_retries:
                break
            jittered = delay * (1 + random.uniform(-0.3, 0.3))
            logger.warning(
                f"[retry_call] {func.__name__} attempt {attempt} failed: {e}. "
                f"Retry in {jittered:.1f}s"
            )
            await asyncio.sleep(jittered)
            delay = min(delay * 2, 8.0)
        except Exception:
            raise

    if fallback is not None:
        return fallback
    raise RetryExhausted(max_retries + 1, last_error)
