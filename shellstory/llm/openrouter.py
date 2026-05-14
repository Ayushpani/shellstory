"""
shellstory.llm.openrouter — OpenRouter LLM client implementation.

OpenRouter exposes an OpenAI-compatible API at https://openrouter.ai/api/v1.
It requires HTTP-Referer and X-Title headers for ranking.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from .base import (
    LLMAuthError,
    LLMClient,
    LLMContextError,
    LLMMessage,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
)

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT = 120.0  # seconds


class OpenRouterClient(LLMClient):
    """
    OpenRouter LLM client.

    Handles:
      - System prompt as first message with role="system"
      - HTTP-Referer / X-Title headers (required by OpenRouter for rankings)
      - JSON mode via response_format
      - Typed error mapping (401 → Auth, 429 → RateLimit, 400 context → Context)
    """

    def __init__(
        self,
        api_key: str,
        model: str = "anthropic/claude-sonnet-4",
        site_url: str = "",
        app_name: str = "shellstory",
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._extra_headers = {
            "HTTP-Referer": site_url or "https://github.com/shellstory/shellstory",
            "X-Title": app_name,
        }

    @property
    def provider_name(self) -> str:
        return "OpenRouter"

    @property
    def model_name(self) -> str:
        return self._model

    async def complete(
        self,
        messages: list[LLMMessage],
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        json_mode: bool = False,
    ) -> LLMResponse:
        payload_messages: list[dict[str, str]] = []

        # OpenRouter accepts system as first message with role="system"
        if system:
            payload_messages.append({"role": "system", "content": system})

        for m in messages:
            payload_messages.append({"role": m.role, "content": m.content})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": payload_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            **self._extra_headers,
        }

        logger.debug(
            "OpenRouter request: model=%s, messages=%d, max_tokens=%d",
            self._model, len(payload_messages), max_tokens,
        )

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(
                    f"{OPENROUTER_BASE}/chat/completions",
                    headers=headers,
                    json=payload,
                )
            except httpx.TimeoutException as e:
                raise LLMProviderError(
                    f"OpenRouter request timed out after {self._timeout}s",
                    status_code=408,
                    raw_response=str(e),
                )
            except httpx.ConnectError as e:
                raise LLMProviderError(
                    "Cannot connect to OpenRouter API — check your internet connection",
                    status_code=0,
                    raw_response=str(e),
                )

        return self._parse_response(resp)

    def _parse_response(self, resp: httpx.Response) -> LLMResponse:
        """Map HTTP response to LLMResponse or raise typed exception."""

        # ── Auth error ───────────────────────────────────────────────────────
        if resp.status_code == 401:
            raise LLMAuthError(
                "OpenRouter: invalid API key. "
                "Check your key at https://openrouter.ai/keys and run 'shellstory configure'."
            )

        if resp.status_code == 403:
            raise LLMAuthError(
                "OpenRouter: access forbidden. Your API key may not have access to this model."
            )

        # ── Rate limit ───────────────────────────────────────────────────────
        if resp.status_code == 429:
            retry_after = None
            raw_retry = resp.headers.get("Retry-After")
            if raw_retry:
                try:
                    retry_after = int(raw_retry)
                except ValueError:
                    pass
            raise LLMRateLimitError("OpenRouter: rate limited", retry_after=retry_after)

        # ── Bad request (context length, invalid model, etc.) ────────────────
        if resp.status_code == 400:
            body = self._safe_json(resp)
            msg = body.get("error", {}).get("message", resp.text[:500])

            if any(kw in msg.lower() for kw in ("context", "token", "length", "too long")):
                raise LLMContextError(f"OpenRouter: context too long — {msg}")

            raise LLMProviderError(
                f"OpenRouter 400: {msg}",
                status_code=400,
                raw_response=resp.text[:1000],
            )

        # ── Server errors ────────────────────────────────────────────────────
        if resp.status_code >= 500:
            raise LLMProviderError(
                f"OpenRouter server error (HTTP {resp.status_code})",
                status_code=resp.status_code,
                raw_response=resp.text[:1000],
            )

        # ── Other non-200 ────────────────────────────────────────────────────
        if resp.status_code != 200:
            raise LLMProviderError(
                f"OpenRouter HTTP {resp.status_code}",
                status_code=resp.status_code,
                raw_response=resp.text[:1000],
            )

        # ── Success ──────────────────────────────────────────────────────────
        body = self._safe_json(resp)

        # OpenRouter sometimes returns an error in a 200 response body
        if "error" in body and "choices" not in body:
            error_msg = body["error"].get("message", str(body["error"]))
            raise LLMProviderError(
                f"OpenRouter returned error in 200 response: {error_msg}",
                status_code=200,
                raw_response=resp.text[:1000],
            )

        try:
            choice = body["choices"][0]
            usage = body.get("usage", {})

            return LLMResponse(
                content=choice["message"]["content"],
                model=body.get("model", self._model),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                finish_reason=choice.get("finish_reason", "stop"),
            )
        except (KeyError, IndexError) as e:
            raise LLMProviderError(
                f"OpenRouter returned unexpected response structure: {e}",
                status_code=200,
                raw_response=resp.text[:1000],
            )

    @staticmethod
    def _safe_json(resp: httpx.Response) -> dict:
        """Parse response body as JSON, returning empty dict on failure."""
        try:
            return resp.json()
        except Exception:
            return {}
