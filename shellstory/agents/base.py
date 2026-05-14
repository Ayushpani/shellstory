"""
shellstory.agents.base — Base class for all LLM agents.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from shellstory.config import load_config
from shellstory.llm.base import LLMMessage
from shellstory.llm.resilient import ResilientLLMClient, AllModelsExhaustedError
from shellstory.models import Session
from shellstory.utils.retry import parse_json_response

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Abstract base class for a ShellStory agent.
    Each agent has a specific role (e.g. "signal", "merger") and uses
    the ResilientLLMClient to execute its task with model fallbacks.
    """

    def __init__(self, agent_name: str, config: Optional[dict[str, Any]] = None):
        self.agent_name = agent_name
        self.config = config or load_config()
        self.llm = ResilientLLMClient(agent_name=agent_name, config=self.config.get("llm", {}))

    @abstractmethod
    async def process(self, session: Session, **kwargs: Any) -> Any:
        """
        Execute the agent's core logic on the given session.
        Subclasses must implement this.
        """
        ...

    async def _call_llm_json(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """Helper to make a standard JSON-mode call."""
        messages = [LLMMessage(role="user", content=user_prompt)]
        
        try:
            response = await self.llm.complete(
                messages=messages,
                system=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=True,
            )
            return parse_json_response(response.content)
            
        except AllModelsExhaustedError as e:
            logger.error("[%s] Agent failed completely: %s", self.agent_name, e)
            return self._fallback_result()

    def _fallback_result(self) -> Any:
        """
        What to return if the LLM completely fails.
        Default is an empty dict, but subclasses should override this
        for graceful degradation.
        """
        return {}
