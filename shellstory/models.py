"""
shellstory.models — Pydantic data models for every layer of the pipeline.

All data flows through these models:
  RawEvent → RedactedEvent → Agent outputs → Runbook → Export
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Raw Capture
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class RawEvent(BaseModel):
    """One event written by the shell hook to the NDJSON capture file."""

    event_type: Literal["command", "output", "error", "session_start", "session_end"]
    timestamp: datetime
    session_id: str
    sequence: int  # monotonically increasing per session

    # command events
    command: Optional[str] = None
    working_dir: Optional[str] = None
    exit_code: Optional[int] = None
    duration_ms: Optional[int] = None

    # output events
    stream: Optional[Literal["stdout", "stderr"]] = None
    text: Optional[str] = None

    # session_start events
    shell: Optional[str] = None  # "bash" | "zsh" | "powershell" | "cmd"
    os: Optional[str] = None  # "Linux" | "macOS" | "Windows"
    hostname: Optional[str] = None
    username: Optional[str] = None
    env_var_names: Optional[list[str]] = None  # names only, never values


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Redacted Form
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class RedactedEvent(BaseModel):
    """RawEvent after PII scrubbing.  Secrets replaced with $VARIABLE_NAME tokens."""

    original_sequence: int
    event_type: str
    timestamp: datetime
    command: Optional[str] = None
    working_dir: Optional[str] = None
    exit_code: Optional[int] = None
    duration_ms: Optional[int] = None
    stream: Optional[str] = None
    text: Optional[str] = None
    shell: Optional[str] = None
    os: Optional[str] = None


class VariableDefinition(BaseModel):
    """One secret replaced by redaction."""

    variable_name: str  # e.g. "DB_PASSWORD"
    original_pattern: str  # e.g. "postgres connection string"
    how_to_set: str  # e.g. "export DB_PASSWORD=<your_value>"


class RedactionResult(BaseModel):
    """Complete output of the redaction pipeline."""

    events: list[RedactedEvent]
    variables: list[VariableDefinition]
    redaction_count: int
    original_event_count: int


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Agent Outputs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class SignalClassification(BaseModel):
    """Output of the Signal Agent for one command."""

    sequence: int
    command: str
    is_signal: bool
    reason: str


class FailureRecord(BaseModel):
    """One failure→recovery cycle found by the Failure Agent."""

    failed_command: str
    exit_code: int
    error_output: str
    recovery_attempts: list[str]
    final_fix: str
    lesson: str  # one-line explanation for the runbook


class PrereqItem(BaseModel):
    """One prerequisite extracted by the Prereq Agent."""

    type: Literal["tool", "env_var", "port", "os", "permission", "service", "other"]
    name: str
    version_constraint: Optional[str] = None
    how_to_check: str
    how_to_install: Optional[str] = None


class RunbookStep(BaseModel):
    """One step in the final runbook."""

    step_number: int
    title: str
    command: Optional[str] = None
    explanation: str
    expected_output: Optional[str] = None
    warning: Optional[str] = None
    retry_note: Optional[str] = None


class Runbook(BaseModel):
    """The final assembled runbook — output of the merger agent."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    title: str
    description: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    variables: list[VariableDefinition]
    prerequisites: list[PrereqItem]
    steps: list[RunbookStep]
    errors_and_fixes: list[FailureRecord]
    raw_signal_commands: list[str]  # for coverage checker


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Validation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class CoverageGap(BaseModel):
    """A signal command not accounted for in the runbook."""

    sequence: int
    command: str
    reason_absent: str  # "noise" | "missing" | "merged_into_step_N"


class CriticalPathGap(BaseModel):
    """A critical command missing from the runbook."""

    command_pattern: str
    reason_critical: str
    suggested_step: str


class ValidationResult(BaseModel):
    """Output of the completeness validation loop."""

    passed: bool
    coverage_gaps: list[CoverageGap]
    critical_path_gaps: list[CriticalPathGap]
    iteration: int


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Session
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class Session(BaseModel):
    """A capture session — one terminal recording."""

    id: str
    title: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    capture_file: str  # path to NDJSON
    status: Literal["capturing", "processing", "complete", "error"]
    shell_type: Optional[str] = None  # "bash" | "zsh" | "powershell" | "cmd" | "ssh"
    runbook_id: Optional[str] = None
    error_message: Optional[str] = None
