"""
shellstory.utils.retry — Generic async retry decorator for LLM calls.

Handles rate limiting with exponential backoff and respects Retry-After headers.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def with_llm_retry(max_attempts: int = 4, base_delay: float = 2.0, max_delay: float = 60.0):
    """
    Decorator for async functions that call LLM providers.

    Retry behaviour:
      - LLMRateLimitError: waits retry_after seconds (from header) or exponential backoff.
      - LLMProviderError with 5xx status: retries with exponential backoff.
      - All other exceptions: re-raised immediately (no retry).

    Args:
        max_attempts: Total number of attempts (including the first).
        base_delay: Initial delay in seconds before first retry.
        max_delay: Maximum delay between retries.

    Usage:
        @with_llm_retry(max_attempts=3)
        async def call_llm(client, messages):
            return await client.complete(messages)
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Import here to avoid circular dependency
            from shellstory.llm.base import LLMProviderError, LLMRateLimitError

            last_exception: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)

                except LLMRateLimitError as e:
                    last_exception = e
                    if attempt >= max_attempts:
                        logger.error("Rate limit exceeded after %d attempts", max_attempts)
                        raise

                    # Use server-provided wait time if available
                    wait = getattr(e, "retry_after", None)
                    if wait is None:
                        wait = min(base_delay * (2 ** (attempt - 1)), max_delay)

                    logger.warning(
                        "Rate limited (attempt %d/%d). Waiting %.1fs...",
                        attempt, max_attempts, wait,
                    )
                    await asyncio.sleep(wait)

                except LLMProviderError as e:
                    last_exception = e
                    status = getattr(e, "status_code", 0)

                    # Only retry on server errors (5xx)
                    if status < 500 or attempt >= max_attempts:
                        raise

                    wait = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.warning(
                        "Provider error %d (attempt %d/%d). Retrying in %.1fs...",
                        status, attempt, max_attempts, wait,
                    )
                    await asyncio.sleep(wait)

            # Should never reach here, but just in case
            if last_exception:
                raise last_exception

        return wrapper  # type: ignore[return-value]

    return decorator


def parse_json_response(content: str) -> dict:
    """
    Parse LLM response as JSON with maximum tolerance.

    Handles common LLM quirks:
      - Markdown code fences (```json ... ```)
      - Leading/trailing explanatory text
      - Extra whitespace

    Used by every agent to parse structured responses.

    Args:
        content: Raw LLM response string.

    Returns:
        Parsed dict, or empty dict if parsing fails entirely.
    """
    import json
    import re

    if not content:
        return {}
        
    text = content.strip()

    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?\s*```\s*$", "", text)
    text = text.strip()

    # Find the outermost JSON structure
    start = None
    for i, c in enumerate(text):
        if c in "{[":
            start = i
            break

    if start is None:
        logger.warning("No JSON structure found in LLM response")
        return {}

    # Find matching end bracket
    end_brace = text.rfind("}")
    end_bracket = text.rfind("]")
    end = max(end_brace, end_bracket)

    if end == -1 or end < start:
        logger.warning("No closing bracket found in LLM response")
        return {}

    json_text = text[start : end + 1]

    try:
        result = json.loads(json_text)
        return result if isinstance(result, dict) else {"data": result}
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed: %s", e)
        return {}
