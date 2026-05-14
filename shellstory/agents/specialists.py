"""
shellstory.agents.specialists — The individual agents that make up the swarm.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from shellstory.models import Session, RawEvent, RunbookStep, VariableDefinition
from shellstory.agents.base import BaseAgent

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Signal Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SignalAgent(BaseAgent):
    """Classifies commands as signal (important) or noise (debugging/navigation)."""
    
    def __init__(self, config=None):
        super().__init__("signal", config)
        
    async def process(self, session: Session, events: list[RawEvent]) -> list[str]:
        # If there are no events, return empty
        if not events:
            return []
            
        # Format events for the prompt
        events_text = ""
        for i, ev in enumerate(events):
            if ev.event_type == "command" and ev.command:
                events_text += f"[{i}] {ev.command}\n"
                
        if not events_text:
            return []

        system_prompt = (
            "You are an expert DevOps engineer. Your job is to classify shell commands "
            "as either 'signal' (essential steps for a runbook, like installing software, "
            "configuring services, or modifying files) or 'noise' (debugging, navigation, "
            "ls, cat, ping, or typos).\n\n"
            "Return ONLY a JSON object with a single key 'signal_indices' containing a "
            "list of integers representing the indices of the signal commands."
        )

        result = await self._call_llm_json(system_prompt, f"Commands:\n{events_text}", max_tokens=1000)
        
        indices = result.get("signal_indices", [])
        
        # Extract the actual commands based on indices
        signal_commands = []
        for i in indices:
            if isinstance(i, int) and 0 <= i < len(events):
                cmd = events[i].command
                if cmd:
                    signal_commands.append(cmd)
                    
        return signal_commands

    def _fallback_result(self) -> Any:
        # Graceful degradation: If LLM fails, assume everything is signal.
        logger.warning("[signal] Degrading to 'all commands are signal'")
        return {"signal_indices": "ALL"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Failure Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FailureAgent(BaseAgent):
    """Finds errors and the commands that fixed them."""
    
    def __init__(self, config=None):
        super().__init__("failure", config)

    async def process(self, session: Session, events: list[RawEvent]) -> list[dict[str, str]]:
        if not events:
            return []
            
        # Extract commands that failed (exit_code != 0) and surrounding context
        # For a real implementation we would send the output too, but keeping it simple for now
        events_text = ""
        for ev in events:
            if ev.event_type == "command" and ev.command:
                status = "FAILED" if ev.exit_code != 0 else "OK"
                events_text += f"[{status}] {ev.command}\n"

        system_prompt = (
            "Analyze these shell commands. Identify any errors (commands marked FAILED) "
            "and the subsequent command(s) that were used to fix them. "
            "Return JSON: {'errors_and_fixes': [{'failed_command': '...', 'exit_code': 1, 'error_output': '...', 'recovery_attempts': [], 'final_fix': '...', 'lesson': '...'}]}"
        )

        result = await self._call_llm_json(system_prompt, events_text, max_tokens=2000)
        return result.get("errors_and_fixes", [])

    def _fallback_result(self) -> Any:
        return {"errors_and_fixes": []}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Sequence Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SequenceAgent(BaseAgent):
    """Reconstructs the logical execution order of steps."""
    
    def __init__(self, config=None):
        super().__init__("sequence", config)

    async def process(self, session: Session, signal_commands: list[str]) -> list[dict[str, str]]:
        if not signal_commands:
            return []

        cmds_text = "\n".join(f"{i}. {cmd}" for i, cmd in enumerate(signal_commands))

        system_prompt = (
            "You are a DevOps documentation expert. Take these raw commands and turn them "
            "into a logical sequence of runbook steps. Group related commands into single steps "
            "if appropriate. Ensure dependencies are in the correct order.\n\n"
            "CRITICAL INSTRUCTION: You MUST NOT invent, hallucinate, or add any commands that are not "
            "in the raw list provided. Use EXACTLY the commands given. Do not add boilerplate (like "
            "`touch file` or `node server`) unless it is explicitly in the raw list.\n\n"
            "Return JSON: {'steps': [{'title': '...', 'command': '...', 'explanation': '...'}]}"
        )

        # Using explicit chain of thought for the primary model (trinity-large-thinking)
        user_prompt = (
            "Commands:\n" + cmds_text + 
            "\n\nFirst, think through the logical order in a <think> block, then output the JSON."
        )

        result = await self._call_llm_json(system_prompt, user_prompt, max_tokens=8000)
        return result.get("steps", [])

    def _fallback_result(self) -> Any:
        # Degradation: Return empty so the Merger knows to use chronological order
        return {"steps": []}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. Prereq Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PrereqAgent(BaseAgent):
    """Extracts prerequisites (tools, ports, env vars)."""
    
    def __init__(self, config=None):
        super().__init__("prereq", config)

    async def process(self, session: Session, events: list[RawEvent]) -> list[str]:
        if not events:
            return []
            
        cmds = [e.command for e in events if e.event_type == "command" and e.command]
        cmds_text = "\n".join(cmds)

        system_prompt = (
            "Analyze these commands and extract all prerequisites required to run this session. "
            "This includes: OS packages (apt, brew), language runtimes, environment variables, "
            "open ports, or specific user permissions.\n\n"
            "Return JSON: {'prerequisites': [{'type': 'tool', 'name': 'Node.js', 'how_to_check': 'node -v'}]}"
        )

        result = await self._call_llm_json(system_prompt, cmds_text, max_tokens=2000)
        return result.get("prerequisites", [])

    def _fallback_result(self) -> Any:
        return {"prerequisites": []}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. Annotation Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AnnotationAgent(BaseAgent):
    """Adds warnings/tips to steps."""
    
    def __init__(self, config=None):
        super().__init__("annotation", config)

    async def process(self, session: Session, steps: list[dict[str, str]]) -> list[dict[str, str]]:
        if not steps:
            return []

        steps_json = json.dumps(steps, indent=2)

        system_prompt = (
            "Review these draft runbook steps. Identify any practical pitfalls, race conditions, "
            "or destructive operations. Add an 'annotation' field to the steps that need warnings or tips. "
            "Return the full list of steps as JSON: {'annotated_steps': [...]}"
        )

        result = await self._call_llm_json(system_prompt, steps_json, max_tokens=4000)
        return result.get("annotated_steps", steps)  # fallback to unannotated steps if key missing

    def _fallback_result(self) -> Any:
        # Fallback is handled by the caller, but return empty dict to be safe
        return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. Merger Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MergerAgent(BaseAgent):
    """Assembles the final runbook from all parts."""
    
    def __init__(self, config=None):
        super().__init__("merger", config)

    async def process(
        self, 
        session: Session, 
        signal_cmds: list[str],
        sequence_steps: list[dict],
        prereqs: list[str],
        errors_fixes: list[dict]
    ) -> dict[str, Any]:
        
        # If sequence failed, build chronological steps
        if not sequence_steps and signal_cmds:
            sequence_steps = [
                {"title": f"Run command {i+1}", "command": cmd, "explanation": ""}
                for i, cmd in enumerate(signal_cmds)
            ]

        data = {
            "session_title": session.title,
            "prerequisites": prereqs,
            "errors_and_fixes": errors_fixes,
            "steps": sequence_steps
        }
        
        data_json = json.dumps(data, indent=2)

        system_prompt = (
            "You are a Senior Technical Writer. Take these components and merge them into "
            "a cohesive, professional Runbook. Refine the descriptions and titles to be clear "
            "and professional.\n\n"
            "CRITICAL INSTRUCTION: DO NOT remove any commands, and DO NOT add or invent any new commands. "
            "Only format the steps provided. If a step seems incomplete, leave it as is.\n\n"
            "Return JSON matching this structure:\n"
            "{'title': '...', 'description': '...', 'prerequisites': [...], "
            "'steps': [{'step_number': 1, 'title': '...', 'command': '...', 'explanation': '...', 'warning': ''}], "
            "'errors_and_fixes': [...], 'variables': []}"
        )

        result = await self._call_llm_json(system_prompt, data_json, max_tokens=16000)
        
        # If the LLM totally failed or missed the steps key, do a programmatic merge
        if not result or "steps" not in result:
            logger.warning("[merger] LLM completely failed or missed steps. Executing programmatic fallback merge.")
            result = {
                "title": session.title,
                "description": "Auto-generated runbook (fallback merge).",
                "prerequisites": prereqs,
                "steps": [
                    {
                        "step_number": i+1,
                        "title": s.get("title", f"Step {i+1}"),
                        "command": s.get("command", ""),
                        "explanation": s.get("explanation", "")
                    }
                    for i, s in enumerate(sequence_steps)
                ],
                "errors_and_fixes": errors_fixes,
                "variables": []
            }
            
        # Ensure Pydantic required fields exist
        result.setdefault("title", session.title)
        result.setdefault("description", "")
        result.setdefault("prerequisites", [])
        result.setdefault("steps", [])
        result.setdefault("errors_and_fixes", [])
        result.setdefault("variables", [])
            
        return result

    def _fallback_result(self) -> Any:
        return {} # Handled explicitly above
