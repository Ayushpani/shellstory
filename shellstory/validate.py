"""
shellstory.validate — Completeness validation and repair loop.

Two-pass validation:
  1. Coverage Checker: Ensures every signal command appears in the runbook.
  2. Critical Path Checker: Identifies missing critical commands
     (e.g., pm2 save, systemctl enable, ufw reload).

If gaps are found, the Repair Agent patches the runbook.
Max 2 repair iterations to avoid infinite loops.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from shellstory.agents.base import BaseAgent
from shellstory.models import Runbook, Session, ValidationResult

logger = logging.getLogger(__name__)

MAX_REPAIR_ITERATIONS = 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Coverage Checker Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class CoverageCheckerAgent(BaseAgent):
    """Verifies every signal command appears in the runbook."""

    def __init__(self, config=None):
        super().__init__("coverage", config)

    async def process(self, session: Session, runbook: Runbook) -> dict[str, Any]:
        signal_cmds = runbook.raw_signal_commands
        if not signal_cmds:
            return {"passed": True, "missing_commands": []}

        # Build the comparison data
        runbook_commands = [
            step.command for step in runbook.steps if step.command
        ]

        data = {
            "signal_commands": signal_cmds,
            "runbook_commands": runbook_commands,
        }

        system_prompt = (
            "Compare these two lists of shell commands. The first list is 'signal_commands' "
            "(important commands from the session). The second is 'runbook_commands' "
            "(commands in the generated runbook).\n\n"
            "For each signal command, determine if it is covered by the runbook "
            "(exact match, or logically merged into another step).\n\n"
            "Return JSON: {'passed': true/false, 'missing_commands': ["
            "{'command': '...', 'reason': 'not found in any step'}]}"
        )

        result = await self._call_llm_json(
            system_prompt, json.dumps(data, indent=2), max_tokens=2000
        )
        return result

    def _fallback_result(self) -> Any:
        return {"passed": True, "missing_commands": []}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Critical Path Checker Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class CriticalPathCheckerAgent(BaseAgent):
    """Identifies critical commands that should be in the runbook but aren't."""

    def __init__(self, config=None):
        super().__init__("critical_path", config)

    async def process(self, session: Session, runbook: Runbook) -> dict[str, Any]:
        steps_text = json.dumps(
            [{"title": s.title, "command": s.command} for s in runbook.steps],
            indent=2,
        )

        system_prompt = (
            "You are a Senior DevOps engineer. Review these runbook steps and identify "
            "any CRITICAL commands that are missing. Critical commands are things like:\n"
            "- pm2 save (after pm2 start)\n"
            "- systemctl enable (after systemctl start)\n"
            "- ufw reload (after ufw allow)\n"
            "- certbot renew setup (after certbot)\n"
            "- nginx -t (before nginx reload)\n"
            "- chmod/chown for security\n\n"
            "Only flag commands that are genuinely missing and important. "
            "Do not flag optional improvements.\n\n"
            "Return JSON: {'has_gaps': true/false, 'missing_critical': ["
            "{'command': '...', 'reason': '...', 'insert_after_step': N}]}"
        )

        result = await self._call_llm_json(
            system_prompt, steps_text, max_tokens=2000
        )
        return result

    def _fallback_result(self) -> Any:
        return {"has_gaps": False, "missing_critical": []}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Repair Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class RepairAgent(BaseAgent):
    """Inserts missing steps into the runbook at the correct positions."""

    def __init__(self, config=None):
        super().__init__("repair", config)

    async def process(
        self,
        session: Session,
        runbook_data: dict[str, Any],
        missing_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not missing_items:
            return runbook_data

        repair_input = {
            "current_steps": runbook_data.get("steps", []),
            "items_to_insert": missing_items,
        }

        system_prompt = (
            "You are a runbook editor. Insert the missing items into the correct "
            "positions in the step list. Renumber all steps sequentially.\n\n"
            "Return JSON: {'steps': [<the full updated list of steps with correct numbering>]}"
        )

        result = await self._call_llm_json(
            system_prompt, json.dumps(repair_input, indent=2), max_tokens=8000
        )

        if "steps" in result:
            runbook_data["steps"] = result["steps"]

        return runbook_data

    def _fallback_result(self) -> Any:
        return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Validation Loop
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def validate_and_repair(
    session: Session, runbook: Runbook, config: dict[str, Any] | None = None
) -> Runbook:
    """
    Run the validation loop:
      1. Check coverage
      2. Check critical path
      3. If gaps found, repair and re-validate (max 2 iterations)
    """
    coverage_checker = CoverageCheckerAgent(config)
    critical_checker = CriticalPathCheckerAgent(config)
    repair_agent = RepairAgent(config)

    current_data = json.loads(runbook.model_dump_json())

    for iteration in range(1, MAX_REPAIR_ITERATIONS + 1):
        logger.info("Validation iteration %d/%d...", iteration, MAX_REPAIR_ITERATIONS)

        # Run both checkers
        coverage = await coverage_checker.process(session, runbook)
        critical = await critical_checker.process(session, runbook)

        missing_from_coverage = coverage.get("missing_commands", [])
        missing_critical = critical.get("missing_critical", [])

        all_missing = []
        for item in missing_from_coverage:
            all_missing.append({
                "command": item.get("command", ""),
                "reason": item.get("reason", "Missing from coverage"),
                "title": f"Run: {item.get('command', 'unknown')}",
                "explanation": item.get("reason", "This command was in the session but missing from the runbook."),
            })
        for item in missing_critical:
            all_missing.append({
                "command": item.get("command", ""),
                "reason": item.get("reason", "Critical path gap"),
                "title": f"Critical: {item.get('command', 'unknown')}",
                "explanation": item.get("reason", "This is a critical command that should be included."),
                "insert_after_step": item.get("insert_after_step"),
            })

        if not all_missing:
            logger.info("Validation passed on iteration %d.", iteration)
            break

        logger.info("Found %d gaps. Running repair agent...", len(all_missing))
        current_data = await repair_agent.process(session, current_data, all_missing)

        # Re-validate with updated runbook
        runbook = Runbook.model_validate(current_data)

    return runbook
