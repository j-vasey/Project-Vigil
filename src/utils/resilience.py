import asyncio
import logging
import random
from typing import Callable, Any, Type, Tuple

logger = logging.getLogger("project_vigil.resilience")


async def retry_with_backoff(
    func: Callable[[], Any],
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retry_exceptions: Tuple[Type[BaseException], ...] = (Exception,)
) -> Any:
    """
    Executes an async callable with exponential backoff retries.

    Args:
        func: Async zero-argument callable to execute.
        max_retries: Maximum number of retry attempts.
        initial_delay: Initial delay before first retry in seconds.
        backoff_factor: Multiplier applied to delay after each retry.
        jitter: If True, adds random jitter to sleep duration.
        retry_exceptions: Tuple of exception types to catch and retry.

    Returns:
        Result of the executed callable.
    """
    delay = initial_delay
    last_exception = None

    for attempt in range(1, max_retries + 1):
        try:
            return await func()
        except retry_exceptions as exc:
            last_exception = exc
            if attempt == max_retries:
                logger.error(
                    f"[Resilience] Maximum retries ({max_retries}) reached. "
                    f"Final error: {exc}"
                )
                raise exc

            sleep_duration = delay
            if jitter:
                sleep_duration += random.uniform(0, delay * 0.5)

            logger.warning(
                f"[Resilience] Attempt {attempt}/{max_retries} failed: {exc}. "
                f"Retrying in {sleep_duration:.2f}s..."
            )
            await asyncio.sleep(sleep_duration)
            delay *= backoff_factor

    if last_exception:
        raise last_exception
