"""Quick validation of Phase 1 + Phase 2 modules."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path


def test_database():
    from shellstory.db import Database
    from shellstory.models import Session

    db = Database(Path(tempfile.mktemp(suffix=".db")))
    s = Session(
        id="test-123",
        title="Deploy auth",
        started_at=datetime.now(timezone.utc),
        capture_file="/tmp/test.ndjson",
        status="capturing",
        shell_type="powershell",
    )
    db.create_session(s)

    # Exact match
    found = db.get_session("test-123")
    assert found is not None and found.title == "Deploy auth"

    # Prefix match
    found_prefix = db.get_session("test")
    assert found_prefix is not None and found_prefix.id == "test-123"

    # List
    sessions = db.list_sessions()
    assert len(sessions) == 1

    # Status update
    db.update_session_status("test-123", "complete", ended_at=datetime.now(timezone.utc))
    updated = db.get_session("test-123")
    assert updated.status == "complete"
    assert updated.ended_at is not None

    db.close()
    print("DB: OK")


def test_json_parser():
    from shellstory.utils.retry import parse_json_response

    # Markdown fences
    r1 = parse_json_response('```json\n{"key": "value"}\n```')
    assert r1 == {"key": "value"}, f"Got {r1}"

    # Leading text
    r2 = parse_json_response('Here is the result: {"a": 1} hope that helps')
    assert r2 == {"a": 1}, f"Got {r2}"

    # Garbage
    r3 = parse_json_response("garbage")
    assert r3 == {}

    # Array wrapped in dict
    r4 = parse_json_response('[1, 2, 3]')
    assert r4 == {"data": [1, 2, 3]}

    print("JSON parser: OK")


def test_llm_factory():
    from shellstory.llm.factory import create_llm_client

    client = create_llm_client({"provider": "openrouter", "api_key": "test-key", "model": "test/model"})
    assert client.provider_name == "OpenRouter"
    assert client.model_name == "test/model"

    client2 = create_llm_client({"provider": "nvidia", "api_key": "nv-key", "model": "meta/llama-3.1-70b-instruct"})
    assert client2.provider_name == "NVIDIA NIM"

    print("LLM factory: OK")


def test_config_merge():
    from shellstory.config import DEFAULT_CONFIG, _deep_merge

    override = {"llm": {"provider": "nvidia", "api_key": "nv-key"}}
    merged = _deep_merge(DEFAULT_CONFIG, override)
    assert merged["llm"]["provider"] == "nvidia"
    assert merged["llm"]["api_key"] == "nv-key"
    assert merged["llm"]["model"] == "anthropic/claude-sonnet-4"  # kept from default
    assert merged["default_connector"] == "markdown"  # untouched default

    print("Config merge: OK")


def test_ndjson():
    from shellstory.models import RawEvent
    from shellstory.utils.ndjson import append_event, count_events, load_events

    tmp = Path(tempfile.mktemp(suffix=".ndjson"))

    # Write events
    for i in range(5):
        append_event(tmp, {
            "event_type": "command",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": "sess-1",
            "sequence": i,
            "command": f"echo {i}",
            "exit_code": 0,
        })

    # Add a bad line
    with open(tmp, "a") as f:
        f.write("this is not json\n")

    events = load_events(tmp)
    assert len(events) == 5
    assert events[0].command == "echo 0"
    assert events[4].sequence == 4

    assert count_events(tmp) == 5

    tmp.unlink()
    print("NDJSON: OK")


def test_models():
    from shellstory.models import Runbook, RunbookStep, VariableDefinition

    runbook = Runbook(
        session_id="sess-1",
        title="Deploy Auth Service",
        description="Deploys the auth service to production.",
        variables=[VariableDefinition(variable_name="DB_PASS", original_pattern="password", how_to_set="export DB_PASS=<val>")],
        prerequisites=[],
        steps=[RunbookStep(step_number=1, title="Install deps", command="npm install", explanation="Install node packages")],
        errors_and_fixes=[],
        raw_signal_commands=["npm install"],
    )

    # Roundtrip JSON
    json_str = runbook.model_dump_json()
    restored = Runbook.model_validate_json(json_str)
    assert restored.title == "Deploy Auth Service"
    assert len(restored.steps) == 1
    assert restored.id == runbook.id

    print("Models: OK")


def test_llm_exceptions():
    from shellstory.llm.base import LLMAuthError, LLMContextError, LLMProviderError, LLMRateLimitError

    # Rate limit with retry_after
    e1 = LLMRateLimitError("rate limited", retry_after=30)
    assert e1.retry_after == 30

    # Provider error with status code
    e2 = LLMProviderError("server error", status_code=502, raw_response="bad gateway")
    assert e2.status_code == 502

    print("LLM exceptions: OK")


if __name__ == "__main__":
    test_database()
    test_json_parser()
    test_llm_factory()
    test_config_merge()
    test_ndjson()
    test_models()
    test_llm_exceptions()
    print("\n[PASS] All Phase 1 + Phase 2 validations PASSED")
