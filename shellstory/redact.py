"""
shellstory.redact — PII redaction pipeline.

Two-stage approach:
  1. Regex-based: Fast, deterministic patterns for known secret formats
     (API keys, AWS keys, tokens, passwords, IPs, emails, etc.)
  2. AI-assisted: Uses the PII Scanner agent for context-aware detection
     (internal hostnames, database URLs that look like normal strings, etc.)

The regex stage catches ~90% of secrets. The AI stage is a safety net.
If the AI stage fails (rate limit, etc.), the regex-only result is used.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from shellstory.agents.base import BaseAgent
from shellstory.models import (
    RawEvent,
    RedactedEvent,
    RedactionResult,
    Session,
    VariableDefinition,
)

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Regex Patterns — ordered from most specific to least
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

REDACTION_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    # AWS Access Key ID
    ("AWS_ACCESS_KEY", "AWS access key", re.compile(r"(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}")),

    # AWS Secret Key
    ("AWS_SECRET_KEY", "AWS secret key", re.compile(r"(?<=[\s=:'\"])[A-Za-z0-9/+=]{40}(?=[\s'\"\n])")),

    # Generic API keys/tokens (long hex/base64 strings after key= or token= etc.)
    ("API_TOKEN", "API token or key",
     re.compile(r"(?i)(?:api[_-]?key|token|secret|password|passwd|auth)\s*[=:]\s*['\"]?([A-Za-z0-9_\-\.]{20,})['\"]?")),

    # Bearer tokens
    ("BEARER_TOKEN", "Bearer auth token",
     re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]+", re.IGNORECASE)),

    # SSH private key markers
    ("SSH_PRIVATE_KEY", "SSH private key",
     re.compile(r"-----BEGIN\s+(RSA|DSA|EC|OPENSSH)\s+PRIVATE\s+KEY-----")),

    # PostgreSQL / MySQL / MongoDB connection strings
    ("DB_CONNECTION_STRING", "Database connection string",
     re.compile(r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?):\/\/[^\s]+")),

    # Generic URLs with credentials (user:pass@host)
    ("URL_WITH_CREDENTIALS", "URL with embedded credentials",
     re.compile(r"https?://[^:]+:[^@]+@[^\s]+")),

    # IPv4 addresses (private ranges especially)
    ("IP_ADDRESS", "IP address",
     re.compile(r"\b(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b")),

    # Email addresses
    ("EMAIL", "Email address",
     re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")),

    # GitHub/GitLab tokens
    ("GIT_TOKEN", "Git platform token",
     re.compile(r"(?:ghp|gho|ghu|ghs|ghr|glpat)[_-][A-Za-z0-9_\-]{20,}")),

    # OpenRouter / OpenAI keys
    ("OPENROUTER_KEY", "OpenRouter API key",
     re.compile(r"sk-or-v1-[a-f0-9]{64}")),

    ("OPENAI_KEY", "OpenAI API key",
     re.compile(r"sk-[A-Za-z0-9]{20,}")),

    # Slack tokens
    ("SLACK_TOKEN", "Slack token",
     re.compile(r"xox[bpors]-[0-9A-Za-z\-]+")),

    # npm tokens
    ("NPM_TOKEN", "npm authentication token",
     re.compile(r"npm_[A-Za-z0-9]{36}")),

    # Passwords in common CLI patterns (--password=xxx, -p xxx, etc.)
    ("CLI_PASSWORD", "CLI password argument",
     re.compile(r"(?i)(?:--password|--passwd|-p)\s*[=\s]\s*\S+")),
]


def regex_redact(text: str) -> tuple[str, list[tuple[str, str, str]]]:
    """
    Apply all regex patterns to a string.

    Returns:
        (redacted_text, list of (variable_name, pattern_desc, matched_value))
    """
    findings: list[tuple[str, str, str]] = []
    result = text

    for var_name, description, pattern in REDACTION_PATTERNS:
        matches = list(pattern.finditer(result))
        for i, match in enumerate(reversed(matches)):  # reverse to preserve positions
            matched_text = match.group(0)
            # For patterns with capture groups, use the group
            if match.lastindex and match.lastindex >= 1:
                matched_text = match.group(1)

            unique_var = f"${var_name}_{len(findings) + 1}" if len(matches) > 1 else f"${var_name}"
            findings.append((unique_var, description, matched_text))

            # Replace in the string
            start, end = match.span()
            result = result[:start] + unique_var + result[end:]

    return result, findings


def redact_event(event: RawEvent) -> tuple[RedactedEvent, list[tuple[str, str, str]]]:
    """Redact a single RawEvent, returning the cleaned event and any findings."""
    all_findings: list[tuple[str, str, str]] = []

    command = event.command
    text = event.text
    working_dir = event.working_dir

    if command:
        command, findings = regex_redact(command)
        all_findings.extend(findings)

    if text:
        text, findings = regex_redact(text)
        all_findings.extend(findings)

    if working_dir:
        working_dir, findings = regex_redact(working_dir)
        all_findings.extend(findings)

    redacted = RedactedEvent(
        original_sequence=event.sequence,
        event_type=event.event_type,
        timestamp=event.timestamp,
        command=command,
        working_dir=working_dir,
        exit_code=event.exit_code,
        duration_ms=event.duration_ms,
        stream=event.stream,
        text=text,
        shell=event.shell,
        os=event.os,
    )

    return redacted, all_findings


def redact_events(events: list[RawEvent]) -> RedactionResult:
    """Run the full regex redaction pipeline on a list of events."""
    redacted_events: list[RedactedEvent] = []
    all_variables: dict[str, VariableDefinition] = {}
    total_redactions = 0

    for event in events:
        redacted, findings = redact_event(event)
        redacted_events.append(redacted)
        total_redactions += len(findings)

        for var_name, description, _matched in findings:
            if var_name not in all_variables:
                # Generate how_to_set based on the variable name
                clean_name = var_name.lstrip("$").rstrip("_0123456789")
                all_variables[var_name] = VariableDefinition(
                    variable_name=var_name,
                    original_pattern=description,
                    how_to_set=f"export {clean_name}=<your_value>",
                )

    return RedactionResult(
        events=redacted_events,
        variables=list(all_variables.values()),
        redaction_count=total_redactions,
        original_event_count=len(events),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AI-Assisted PII Scanner Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class PIIScannerAgent(BaseAgent):
    """
    AI-powered PII scanner that catches secrets the regex patterns miss.

    Uses Gemma 4 31B (lowest hallucination) as primary model.
    """

    def __init__(self, config=None):
        super().__init__("pii_scanner", config)

    async def process(self, session: Session, events: list[RawEvent]) -> list[dict[str, str]]:
        """Scan for additional PII not caught by regex."""
        if not events:
            return []

        # Build a compact representation of the session for the LLM
        lines = []
        for ev in events:
            if ev.event_type == "command" and ev.command:
                lines.append(f"CMD: {ev.command}")
            elif ev.event_type == "output" and ev.text:
                # Truncate long outputs
                text = ev.text[:500] if len(ev.text) > 500 else ev.text
                lines.append(f"OUT: {text}")

        if not lines:
            return []

        session_text = "\n".join(lines[:200])  # Cap at 200 lines to stay within context

        system_prompt = (
            "You are a security expert reviewing terminal session logs. "
            "Identify any sensitive information that should be redacted before "
            "this log is turned into documentation. Look for:\n"
            "- Internal hostnames or IP addresses\n"
            "- Database names that reveal business logic\n"
            "- Usernames that aren't generic\n"
            "- File paths containing personal info\n"
            "- Any tokens/keys that might not match standard patterns\n\n"
            "Return JSON: {'additional_secrets': [{'text': 'the exact text to redact', "
            "'reason': 'why this is sensitive', 'suggested_variable': '$VAR_NAME'}]}\n"
            "If nothing additional is found, return {'additional_secrets': []}"
        )

        result = await self._call_llm_json(system_prompt, session_text, max_tokens=2000)
        return result.get("additional_secrets", [])

    def _fallback_result(self) -> Any:
        # If the AI scanner fails, regex-only is still 90%+ effective
        logger.warning("[pii_scanner] AI scanner failed. Using regex-only redaction.")
        return {"additional_secrets": []}
