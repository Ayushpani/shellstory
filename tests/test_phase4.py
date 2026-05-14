"""Tests for Phase 4: Capture, Redaction, Validation, CLI, and Connectors."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from shellstory.capture import (
    create_hook_file,
    detect_shell,
    generate_bash_hook,
    generate_powershell_hook,
    generate_zsh_hook,
)
from shellstory.connectors import MarkdownConnector, get_connector
from shellstory.models import (
    FailureRecord,
    PrereqItem,
    Runbook,
    RunbookStep,
    VariableDefinition,
    RawEvent,
    RedactionResult,
)
from shellstory.redact import redact_events, regex_redact


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Capture Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCapture:
    def test_powershell_hook_generation(self):
        script = generate_powershell_hook("sess-123", "/tmp/cap.ndjson")
        assert "sess-123" in script
        assert "__ShellStory_WriteEvent" in script
        assert "PowerShell.Exiting" in script
        assert "session_start" in script

    def test_bash_hook_generation(self):
        script = generate_bash_hook("sess-456", "/tmp/cap.ndjson")
        assert "sess-456" in script
        assert "__shellstory_preexec" in script
        assert "__shellstory_precmd" in script
        assert "PROMPT_COMMAND" in script

    def test_zsh_hook_generation(self):
        script = generate_zsh_hook("sess-789", "/tmp/cap.ndjson")
        assert "sess-789" in script
        assert "add-zsh-hook" in script
        assert "preexec" in script

    def test_create_hook_file(self, tmp_path):
        hook_path = create_hook_file("sess-abc", "/tmp/cap.ndjson", "powershell", tmp_path)
        assert hook_path.exists()
        assert hook_path.suffix == ".ps1"
        content = hook_path.read_text()
        assert "sess-abc" in content

    def test_create_hook_file_bash(self, tmp_path):
        hook_path = create_hook_file("sess-def", "/tmp/cap.ndjson", "bash", tmp_path)
        assert hook_path.suffix == ".sh"

    def test_create_hook_file_invalid_shell(self, tmp_path):
        with pytest.raises(ValueError, match="Unsupported shell"):
            create_hook_file("sess-ghi", "/tmp/cap.ndjson", "cmd", tmp_path)

    def test_detect_shell_windows(self):
        with patch("shellstory.capture.platform") as mock_platform:
            mock_platform.system.return_value = "Windows"
            with patch.dict("os.environ", {"PSModulePath": "C:\\something"}):
                assert detect_shell() == "powershell"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PII Redaction Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRedaction:
    def test_aws_key_redaction(self):
        text = "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        redacted, findings = regex_redact(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in redacted
        assert len(findings) >= 1

    def test_bearer_token_redaction(self):
        text = "curl -H 'Authorization: Bearer sk-abcdef123456789'"
        redacted, findings = regex_redact(text)
        assert "sk-abcdef123456789" not in redacted

    def test_openrouter_key_redaction(self):
        text = "export OPENROUTER_API_KEY=sk-or-v1-" + "a" * 64
        redacted, findings = regex_redact(text)
        assert "sk-or-v1" not in redacted

    def test_db_connection_string(self):
        text = "psql postgres://admin:secret@db.internal:5432/mydb"
        redacted, findings = regex_redact(text)
        assert "admin:secret" not in redacted

    def test_email_redaction(self):
        text = "git config user.email john.doe@company.com"
        redacted, findings = regex_redact(text)
        assert "john.doe@company.com" not in redacted

    def test_private_ip_redaction(self):
        text = "ssh user@192.168.1.100"
        redacted, findings = regex_redact(text)
        assert "192.168.1.100" not in redacted

    def test_github_token_redaction(self):
        text = "export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        redacted, findings = regex_redact(text)
        assert "ghp_" not in redacted

    def test_no_false_positive_on_normal_text(self):
        text = "npm install express"
        redacted, findings = regex_redact(text)
        assert redacted == text
        assert len(findings) == 0

    def test_full_pipeline_redaction(self):
        events = [
            RawEvent(
                event_type="command",
                timestamp=datetime.now(timezone.utc),
                session_id="s1",
                sequence=1,
                command="export DB_URL=postgres://admin:p4ssw0rd@10.0.0.5:5432/prod",
            ),
            RawEvent(
                event_type="command",
                timestamp=datetime.now(timezone.utc),
                session_id="s1",
                sequence=2,
                command="npm install",
            ),
        ]
        result = redact_events(events)
        assert isinstance(result, RedactionResult)
        assert result.redaction_count >= 1
        assert result.original_event_count == 2
        # The connection string should be redacted
        assert "p4ssw0rd" not in (result.events[0].command or "")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Markdown Connector Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestMarkdownConnector:
    @pytest.fixture
    def sample_runbook(self):
        return Runbook(
            session_id="sess-test",
            title="Deploy Node.js to Production",
            description="Complete deployment runbook for the API server.",
            variables=[
                VariableDefinition(
                    variable_name="$DB_PASSWORD",
                    original_pattern="PostgreSQL password",
                    how_to_set="export DB_PASSWORD=<your_value>",
                )
            ],
            prerequisites=[
                PrereqItem(
                    type="tool",
                    name="Node.js",
                    version_constraint=">=18.0",
                    how_to_check="node -v",
                    how_to_install="apt install nodejs",
                )
            ],
            steps=[
                RunbookStep(
                    step_number=1,
                    title="Install dependencies",
                    command="npm install --production",
                    explanation="Install production Node.js packages.",
                    warning="Ensure you are in the project root.",
                ),
                RunbookStep(
                    step_number=2,
                    title="Start PM2",
                    command="pm2 start ecosystem.config.js",
                    explanation="Launch the application with PM2 process manager.",
                ),
            ],
            errors_and_fixes=[
                FailureRecord(
                    failed_command="npm install",
                    exit_code=1,
                    error_output="EACCES permission denied",
                    recovery_attempts=["sudo npm install"],
                    final_fix="sudo chown -R $USER /usr/local/lib/node_modules",
                    lesson="Fix npm permissions instead of using sudo for install.",
                )
            ],
            raw_signal_commands=["npm install", "pm2 start"],
        )

    def test_markdown_export(self, sample_runbook, tmp_path):
        config = {"connectors": {"markdown": {"output_dir": str(tmp_path)}}}
        connector = MarkdownConnector()
        output_path = connector.export(sample_runbook, config)

        assert Path(output_path).exists()
        content = Path(output_path).read_text(encoding="utf-8")

        # Check structure
        assert "# Deploy Node.js to Production" in content
        assert "## Prerequisites" in content
        assert "## Steps" in content
        assert "## Troubleshooting" in content
        assert "## Environment Variables" in content

        # Check content
        assert "npm install --production" in content
        assert "pm2 start ecosystem.config.js" in content
        assert "$DB_PASSWORD" in content
        assert "Node.js" in content
        assert "EACCES" in content

    def test_get_connector(self):
        connector = get_connector("markdown")
        assert isinstance(connector, MarkdownConnector)

    def test_get_invalid_connector(self):
        with pytest.raises(ValueError, match="Unknown connector"):
            get_connector("notion")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCLI:
    def test_cli_help(self):
        from click.testing import CliRunner
        from shellstory.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "ShellStory" in result.output

    def test_cli_version(self):
        from click.testing import CliRunner
        from shellstory.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_cli_list_empty(self):
        from click.testing import CliRunner
        from shellstory.cli import main

        runner = CliRunner()
        # Use a temp DB to avoid polluting real data
        with patch("shellstory.cli.Database") as MockDB:
            mock_db = MockDB.return_value
            mock_db.list_sessions.return_value = []
            mock_db.close.return_value = None

            result = runner.invoke(main, ["list"])
            assert result.exit_code == 0

    def test_cli_status_no_session(self):
        from click.testing import CliRunner
        from shellstory.cli import main

        runner = CliRunner()
        with patch("shellstory.cli.Database") as MockDB:
            mock_db = MockDB.return_value
            mock_db.get_active_session.return_value = None
            mock_db.close.return_value = None

            result = runner.invoke(main, ["status"])
            assert result.exit_code == 0
