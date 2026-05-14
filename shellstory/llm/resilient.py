"""
shellstory.llm.resilient — Resilience wrapper for LLM clients.

Handles:
- Model fallback chains (if deepseek fails, try gemma)
- Rate limiting (staggering calls to stay under 20 req/min)
- Context window chunking logic
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from .base import (
    LLMClient,
    LLMContextError,
    LLMError,
    LLMMessage,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
)
from .factory import create_llm_client
from .models import get_agent_chain

logger = logging.getLogger(__name__)


class AllModelsExhaustedError(LLMError):
    """Raised when all models in a fallback chain fail."""
    pass


class RateLimiter:
    """
    Global rate limiter for free tier usage.
    Ensures we don't exceed max_requests_per_minute across all agents.
    """

    def __init__(self, max_requests_per_minute: int = 18):
        # Leave a small margin (2) from the typical 20/min limit
        self.max_requests = max_requests_per_minute
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until it is safe to make a request."""
        async with self._lock:
            now = time.time()
            
            # Prune old timestamps
            self._timestamps = [t for t in self._timestamps if now - t < 60.0]
            
            if len(self._timestamps) >= self.max_requests:
                # Need to wait until the oldest request falls out of the 60s window
                wait_time = 60.0 - (now - self._timestamps[0])
                if wait_time > 0:
                    logger.debug("Global rate limit reached. Waiting %.1fs", wait_time)
                    await asyncio.sleep(wait_time)
                
                # After waiting, clean up again
                now = time.time()
                self._timestamps = [t for t in self._timestamps if now - t < 60.0]

            self._timestamps.append(time.time())

# A global instance used by all resilient clients
global_rate_limiter = RateLimiter()


class ResilientLLMClient(LLMClient):
    """
    Wraps multiple LLMClients to provide model fallbacks.
    Also handles global rate limiting.
    """

    def __init__(self, agent_name: str, config: dict[str, Any]):
        """
        Args:
            agent_name: e.g. "signal", "merger". Determines the model chain.
            config: The full "llm" section from config.yaml
        """
        self.agent_name = agent_name
        self.config = config
        
        # Check if user provided an override in config
        overrides = config.get("model_overrides", {})
        if agent_name in overrides:
            self.model_chain = [overrides[agent_name]]
            logger.debug("Using user override model %s for %s", self.model_chain[0], agent_name)
        else:
            self.model_chain = get_agent_chain(agent_name)
            
        self._current_client: Optional[LLMClient] = None
        self._current_model_idx = 0

    @property
    def provider_name(self) -> str:
        return self._current_client.provider_name if self._current_client else "ResilientWrapper"

    @property
    def model_name(self) -> str:
        return self.model_chain[self._current_model_idx]

    def _create_client_for_model(self, model_id: str) -> LLMClient:
        """Create a standard client but override the model string."""
        client_config = dict(self.config)
        client_config["model"] = model_id
        
        # Free models are predominantly OpenRouter
        if not client_config.get("provider"):
            client_config["provider"] = "openrouter"
            
        return create_llm_client(client_config)

    async def complete(
        self,
        messages: list[LLMMessage],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        json_mode: bool = False,
    ) -> LLMResponse:
        """
        Attempt completion with the fallback chain.
        """
        last_error: Optional[Exception] = None

        for idx, model_id in enumerate(self.model_chain):
            self._current_model_idx = idx
            self._current_client = self._create_client_for_model(model_id)

            try:
                # Wait for global rate limit capacity
                await global_rate_limiter.acquire()
                
                logger.info(
                    "[%s] Calling %s (fallback %d/%d)", 
                    self.agent_name, model_id, idx + 1, len(self.model_chain)
                )
                
                response = await self._current_client.complete(
                    messages=messages,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    json_mode=json_mode,
                )
                return response

            except LLMRateLimitError as e:
                logger.warning("[%s] Model %s rate limited: %s", self.agent_name, model_id, e)
                last_error = e
                # Immediately try next model in chain
                continue

            except LLMContextError as e:
                logger.warning("[%s] Context too large for %s: %s", self.agent_name, model_id, e)
                last_error = e
                # Note: A real implementation might try to chunk here, but for now we fallback
                # to a model with a potentially larger context window.
                continue

            except LLMProviderError as e:
                # 5xx errors should trigger fallback. 400s (bad request) probably shouldn't, 
                # but we'll try the fallback anyway just in case it's a model quirk.
                logger.warning("[%s] Provider error from %s: %s", self.agent_name, model_id, e)
                last_error = e
                continue
                
            except Exception as e:
                # Network errors etc.
                logger.warning("[%s] Unexpected error from %s: %s", self.agent_name, model_id, e)
                last_error = e
                continue

        # If we get here, all models failed
        logger.error("[%s] All models in fallback chain exhausted.", self.agent_name)
        raise AllModelsExhaustedError(
            f"Agent '{self.agent_name}' failed after trying {len(self.model_chain)} models. "
            f"Last error: {last_error}"
        ) from last_error
