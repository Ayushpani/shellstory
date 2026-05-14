"""
shellstory.llm.factory — Provider selection from configuration.

Usage:
    config = load_config()
    client = create_llm_client(config["llm"])
    response = await client.complete(messages=[...])
"""

from __future__ import annotations

from typing import Any

from .base import LLMClient


def create_llm_client(llm_config: dict[str, Any]) -> LLMClient:
    """
    Create an LLM client from the ``llm`` section of the config.

    Required keys:
        provider: "openrouter" | "nvidia"
        api_key:  The API key string
        model:    The model identifier string

    Optional keys (OpenRouter only):
        site_url: Your site URL for rankings
        app_name: Your app name for rankings

    Returns:
        Configured LLMClient instance.

    Raises:
        ValueError: Unknown or missing provider.
    """
    provider = llm_config.get("provider", "").lower().strip()
    api_key = llm_config.get("api_key", "")
    model = llm_config.get("model", "")

    if not provider:
        raise ValueError(
            "LLM provider not configured. "
            "Run 'shellstory configure' to set up your provider (openrouter or nvidia)."
        )

    if not api_key:
        raise ValueError(
            f"API key not configured for provider '{provider}'. "
            "Run 'shellstory configure' to set your API key."
        )

    if provider == "openrouter":
        from .openrouter import OpenRouterClient

        return OpenRouterClient(
            api_key=api_key,
            model=model or "anthropic/claude-sonnet-4",
            site_url=llm_config.get("site_url", ""),
            app_name=llm_config.get("app_name", "shellstory"),
        )

    elif provider == "nvidia":
        from .nvidia import NvidiaClient

        return NvidiaClient(
            api_key=api_key,
            model=model or "meta/llama-3.1-70b-instruct",
        )

    else:
        raise ValueError(
            f"Unknown LLM provider '{provider}'. "
            "Must be 'openrouter' or 'nvidia'. Run 'shellstory configure' to fix."
        )
