"""
shellstory.llm.base — Abstract LLM client interface and exception hierarchy.

All agents call `llm_client.complete(messages, system)`. They never touch
provider-specific APIs directly. This module defines the contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data structures
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass(frozen=True, slots=True)
class LLMMessage:
    """A single message in a chat conversation."""

    role: str  # "user" | "assistant" | "system"
    content: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Parsed response from an LLM provider."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    finish_reason: str  # "stop" | "length" | "error"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Exception hierarchy
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class LLMError(Exception):
    """Base exception for all LLM-related errors."""

    pass


class LLMRateLimitError(LLMError):
    """HTTP 429 — rate limited. Caller should back off."""

    def __init__(self, message: str = "Rate limited", retry_after: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after


class LLMContextError(LLMError):
    """Input too long for model's context window. Caller should chunk."""

    pass


class LLMAuthError(LLMError):
    """Invalid API key or permissions. Fatal — surface to user immediately."""

    pass


class LLMProviderError(LLMError):
    """Any other provider error (5xx, unexpected responses, etc.)."""

    def __init__(self, message: str = "Provider error", status_code: int = 0, raw_response: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.raw_response = raw_response


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Abstract client
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class LLMClient(ABC):
    """
    Abstract base for all LLM provider clients.

    All agents interact exclusively through this interface.
    Never call provider APIs directly outside the `llm/` package.
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        json_mode: bool = False,
    ) -> LLMResponse:
        """
        Send a chat completion request.

        Args:
            messages:    Conversation history. Do NOT include a system message here;
                         pass it via the ``system`` param instead.
            system:      System prompt. Injected at the correct position per provider.
            max_tokens:  Hard output token limit.
            temperature: 0.0 = deterministic, 0.1 = default for structured tasks.
            json_mode:   If True, instruct provider to return valid JSON only.

        Returns:
            LLMResponse with the completion content.

        Raises:
            LLMRateLimitError:  HTTP 429. Caller should back off.
            LLMContextError:    Input too long. Caller should chunk.
            LLMAuthError:       Bad API key. Fatal; surface to user.
            LLMProviderError:   Any other provider error.
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name for logs and error messages."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The model string currently configured."""
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} provider={self.provider_name!r} model={self.model_name!r}>"
