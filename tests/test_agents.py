"""Test the agent swarm and resilience logic."""

import asyncio
import tempfile
import json
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from shellstory.config import DEFAULT_CONFIG, _deep_merge
from shellstory.models import Session, RawEvent
from shellstory.llm.resilient import ResilientLLMClient, AllModelsExhaustedError
from shellstory.llm.base import LLMRateLimitError, LLMResponse, LLMMessage
from shellstory.agents.swarm import SwarmOrchestrator


@pytest.fixture
def mock_config():
    return _deep_merge(
        DEFAULT_CONFIG,
        {
            "llm": {
                "provider": "openrouter",
                "api_key": "test",
                "model_overrides": {
                    "signal": "test/signal-model"
                }
            },
            "sessions_dir": tempfile.mkdtemp()
        }
    )

@pytest.fixture
def test_session():
    return Session(
        id="sess-123",
        title="Test session",
        started_at=datetime.now(timezone.utc),
        capture_file="/tmp/cap.ndjson",
        status="capturing",
        shell_type="bash"
    )

@pytest.fixture
def test_events():
    return [
        RawEvent(
            session_id="sess-123",
            sequence=1,
            timestamp=datetime.now(timezone.utc),
            event_type="command",
            command="npm install",
            exit_code=0
        ),
        RawEvent(
            session_id="sess-123",
            sequence=2,
            timestamp=datetime.now(timezone.utc),
            event_type="command",
            command="pm2 start",
            exit_code=0
        )
    ]

@pytest.mark.asyncio
async def test_resilient_client_fallback(mock_config):
    """Test that the resilient client correctly falls back when rate limited."""
    client = ResilientLLMClient("coverage", mock_config["llm"])
    # Coverage chain is ["google/gemma-4-31b-it:free", "z-ai/glm-4.5-air:free", ...]
    assert client.model_chain[0] == "google/gemma-4-31b-it:free"

    # Mock the underlying OpenRouterClient.complete
    with patch('shellstory.llm.openrouter.OpenRouterClient.complete') as mock_complete:
        # First call fails with rate limit, second succeeds
        mock_complete.side_effect = [
            LLMRateLimitError("Rate limited"),
            LLMResponse(content='{"result": "ok"}', model="z-ai/glm-4.5-air:free", input_tokens=10, output_tokens=10, finish_reason="stop")
        ]

        resp = await client.complete([LLMMessage("user", "test")])
        
        assert mock_complete.call_count == 2
        assert '{"result": "ok"}' in resp.content
        assert client.model_name == "z-ai/glm-4.5-air:free"


@pytest.mark.asyncio
async def test_resilient_client_exhaustion(mock_config):
    """Test behavior when all models fail."""
    client = ResilientLLMClient("signal", mock_config["llm"])
    # We overrode signal to just 1 model in the config
    assert len(client.model_chain) == 1

    with patch('shellstory.llm.openrouter.OpenRouterClient.complete') as mock_complete:
        mock_complete.side_effect = LLMRateLimitError("Rate limited")

        with pytest.raises(AllModelsExhaustedError):
            await client.complete([LLMMessage("user", "test")])


@pytest.mark.asyncio
async def test_swarm_orchestrator(mock_config, test_session, test_events):
    """Test the full swarm pipeline with mocked LLM returns."""
    
    orchestrator = SwarmOrchestrator(mock_config)
    
    # We will mock ResilientLLMClient._call_llm_json directly
    # to avoid needing to construct HTTP responses.
    
    async def mock_call_llm_json(*args, **kwargs):
        # We can distinguish the agent by inspecting args or just return a generic dict
        # Actually, let's mock BaseAgent._call_llm_json instead
        pass

    with patch('shellstory.agents.base.BaseAgent._call_llm_json') as mock_llm:
        
        def side_effect(system_prompt, user_prompt, **kwargs):
            if "signal" in system_prompt.lower():
                return {"signal_indices": [0, 1]}
            elif "identify any errors" in system_prompt.lower():
                return {"errors_and_fixes": [{"failed_command": "npm typo", "exit_code": 1, "error_output": "err", "recovery_attempts": [], "final_fix": "npm install", "lesson": "Use install"}]}
            elif "sequence" in system_prompt.lower():
                return {"steps": [{"title": "Install", "command": "npm install", "explanation": "..."}]}
            elif "extract all prerequisites" in system_prompt.lower():
                return {"prerequisites": [{"type": "tool", "name": "node", "how_to_check": "node -v"}]}
            elif "pitfalls" in system_prompt.lower():
                return {"annotated_steps": [{"title": "Install", "command": "npm install", "explanation": "...", "warning": "Warning"}]}
            elif "technical writer" in system_prompt.lower():
                return {
                    "title": "Test Runbook",
                    "description": "Merged.",
                    "prerequisites": [{"type": "tool", "name": "node", "how_to_check": "node -v"}],
                    "steps": [{"step_number": 1, "title": "Install", "command": "npm install", "explanation": "...", "warning": "Warning"}],
                    "errors_and_fixes": [{"failed_command": "npm typo", "exit_code": 1, "error_output": "err", "recovery_attempts": [], "final_fix": "npm install", "lesson": "Use install"}],
                    "variables": []
                }
            return {}
            
        mock_llm.side_effect = side_effect
        
        # We need to patch asyncio.sleep to not actually wait 3/6 seconds during testing
        with patch('asyncio.sleep'):
            runbook = await orchestrator.run(test_session, test_events)
            
            # Verify the resulting runbook
            assert runbook.title == "Test Runbook"
            assert len(runbook.steps) == 1
            assert runbook.steps[0].warning == "Warning"
            
            # Verify checkpoints were created
            assert Path(mock_config["sessions_dir"], "sess-123.agents.json").exists()
            assert Path(mock_config["sessions_dir"], "sess-123.runbook.json").exists()
            
            # If we run it again, it should skip right to the end by loading the runbook checkpoint
            mock_llm.reset_mock()
            runbook_cached = await orchestrator.run(test_session, test_events)
            assert runbook_cached.title == "Test Runbook"
            assert mock_llm.call_count == 0  # didn't call LLM because checkpoint exists
