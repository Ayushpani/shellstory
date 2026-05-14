"""
shellstory.llm — LLM provider abstraction layer.

Public API:
    from shellstory.llm import create_llm_client, LLMClient, LLMMessage, LLMResponse
    from shellstory.llm import LLMError, LLMAuthError, LLMRateLimitError, LLMContextError, LLMProviderError
"""

from .base import (
    LLMAuthError,
    LLMClient,
    LLMContextError,
    LLMError,
    LLMMessage,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
)
from .factory import create_llm_client

__all__ = [
    "create_llm_client",
    "LLMClient",
    "LLMMessage",
    "LLMResponse",
    "LLMError",
    "LLMAuthError",
    "LLMRateLimitError",
    "LLMContextError",
    "LLMProviderError",
]
