"""
shellstory.llm.nvidia — NVIDIA NIM LLM client implementation.

NVIDIA NIM uses the OpenAI-compatible endpoint at https://integrate.api.nvidia.com/v1.
Model strings look like: "meta/llama-3.1-405b-instruct".
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

NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
DEFAULT_TIMEOUT = 180.0  # NIM can be slower than cloud providers


class NvidiaClient(LLMClient):
    """
    NVIDIA NIM LLM client.

    Handles:
      - System prompt as first message with role="system"
      - JSON mode: both response_format AND inline instruction (fallback)
      - Non-streaming mode for simplicity
      - Typed error mapping matching the base interface
    """

    def __init__(
        self,
        api_key: str,
        model: str = "meta/llama-3.1-70b-instruct",
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    @property
    def provider_name(self) -> str:
        return "NVIDIA NIM"

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

        if system:
            payload_messages.append({"role": "system", "content": system})

        for m in messages:
            payload_messages.append({"role": m.role, "content": m.content})

        # For JSON mode: append inline instruction as fallback
        # (not all NIM models support response_format)
        if json_mode and payload_messages and payload_messages[-1]["role"] == "user":
            payload_messages[-1]["content"] += (
                "\n\nRespond with valid JSON only. No preamble, no markdown fences."
            )

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": payload_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }

        # Attempt standard response_format — models that support it will use it
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        logger.debug(
            "NVIDIA NIM request: model=%s, messages=%d, max_tokens=%d",
            self._model, len(payload_messages), max_tokens,
        )

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(
                    f"{NVIDIA_BASE}/chat/completions",
                    headers=headers,
                    json=payload,
                )
            except httpx.TimeoutException as e:
                raise LLMProviderError(
                    f"NVIDIA NIM request timed out after {self._timeout}s",
                    status_code=408,
                    raw_response=str(e),
                )
            except httpx.ConnectError as e:
                raise LLMProviderError(
                    "Cannot connect to NVIDIA NIM API — check your internet connection",
                    status_code=0,
                    raw_response=str(e),
                )

        return self._parse_response(resp)

    def _parse_response(self, resp: httpx.Response) -> LLMResponse:
        """Map HTTP response to LLMResponse or raise typed exception."""

        if resp.status_code == 401:
            raise LLMAuthError(
                "NVIDIA NIM: invalid API key. "
                "Get one at https://build.nvidia.com and run 'shellstory configure'."
            )

        if resp.status_code == 403:
            raise LLMAuthError(
                "NVIDIA NIM: access forbidden. Your API key may not have access to this model."
            )

        if resp.status_code == 429:
            retry_after = None
            raw_retry = resp.headers.get("Retry-After")
            if raw_retry:
                try:
                    retry_after = int(raw_retry)
                except ValueError:
                    pass
            raise LLMRateLimitError("NVIDIA NIM: rate limited", retry_after=retry_after)

        if resp.status_code == 400:
            body = self._safe_json(resp)
            detail = body.get("detail", body.get("error", {}).get("message", resp.text[:500]))
            if isinstance(detail, dict):
                detail = str(detail)

            if any(kw in detail.lower() for kw in ("context", "token", "length", "too long")):
                raise LLMContextError(f"NVIDIA NIM: context too long — {detail}")

            raise LLMProviderError(
                f"NVIDIA NIM 400: {detail}",
                status_code=400,
                raw_response=resp.text[:1000],
            )

        if resp.status_code >= 500:
            raise LLMProviderError(
                f"NVIDIA NIM server error (HTTP {resp.status_code})",
                status_code=resp.status_code,
                raw_response=resp.text[:1000],
            )

        if resp.status_code != 200:
            raise LLMProviderError(
                f"NVIDIA NIM HTTP {resp.status_code}",
                status_code=resp.status_code,
                raw_response=resp.text[:1000],
            )

        # ── Success ──────────────────────────────────────────────────────────
        body = self._safe_json(resp)

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
                f"NVIDIA NIM returned unexpected response structure: {e}",
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
