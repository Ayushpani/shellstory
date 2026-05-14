"""
shellstory.agents.swarm — Orchestration for the agent swarm.

Handles parallel execution of the independent agents, staggering them
to avoid bursting rate limits, and then chains the sequential agents (merger).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from shellstory.config import load_config, get_sessions_dir
from shellstory.models import Session, RawEvent, Runbook
from shellstory.agents.specialists import (
    SequenceAgent,
    AnnotationAgent,
    MergerAgent,
    SignalAgent,
    FailureAgent,
    PrereqAgent,
)

logger = logging.getLogger(__name__)


class SwarmOrchestrator:
    """Manages the lifecycle and data flow between all agents."""

    def __init__(self, config: dict[str, Any] | None = None, dashboard: Any = None):
        self.config = config or load_config()
        self.sessions_dir = get_sessions_dir(self.config)
        self.dashboard = dashboard

    def _get_checkpoint_path(self, session_id: str, stage: str) -> Path:
        return self.sessions_dir / f"{session_id}.{stage}.json"

    async def run(self, session: Session, events: list[RawEvent]) -> Runbook:
        """Run the complete agent swarm pipeline."""
        logger.info(f"Starting agent swarm for session {session.id}...")
        
        state_file = self._get_checkpoint_path(session.id, "state")
        agents_cp = self._get_checkpoint_path(session.id, "agents")
        
        signal_cmds = []
        errors_fixes = []
        prereqs = []
        
        # ── Stage 0: Load from Daemon State or Run Fallback ──────────────────
        if agents_cp.exists():
            logger.info("Found agents checkpoint for %s, skipping extraction phase.", session.id)
            with open(agents_cp, "r", encoding="utf-8") as f:
                swarm_results = json.load(f)
            signal_cmds = swarm_results.get("signal", [])
            errors_fixes = swarm_results.get("failure", [])
            prereqs = swarm_results.get("prereq", [])
        elif state_file.exists():
            logger.info("Stage 1: Loading pre-computed state from background daemon...")
            try:
                with open(state_file, encoding="utf-8") as f:
                    state = json.load(f)
                signal_cmds = state.get("signal_commands", [])
                errors_fixes = state.get("errors_and_fixes", [])
                prereqs = state.get("prerequisites", [])
                processed_count = state.get("_processed_count", 0)
                
                # Check for unprocessed events (the race condition tail)
                if processed_count < len(events):
                    logger.info(f"Catching up: Processing {len(events) - processed_count} events the daemon missed...")
                    unprocessed_events = events[processed_count:]
                    tail_results = await self._run_parallel_swarm(session, unprocessed_events)
                    
                    signal_cmds.extend(tail_results.get("signal", []))
                    errors_fixes.extend(tail_results.get("failure", []))
                    for p in tail_results.get("prereq", []):
                        if p not in prereqs:
                            prereqs.append(p)
                
                # Save it as agents_cp so we don't reload next time
                swarm_results = {
                    "signal": signal_cmds,
                    "failure": errors_fixes,
                    "prereq": prereqs
                }
                with open(agents_cp, "w", encoding="utf-8") as f:
                    json.dump(swarm_results, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to load daemon state: {e}. Falling back to full processing.")
                swarm_results = await self._run_parallel_swarm(session, events)
                signal_cmds = swarm_results.get("signal", [])
        else:
            logger.warning("No daemon state found. Running full parallel extraction from scratch...")
            swarm_results = await self._run_parallel_swarm(session, events)
            signal_cmds = swarm_results.get("signal", [])

        # ── Stage 2: Sequence and Annotation ─────────────────────────────────
        if "sequence" not in swarm_results:
            sequence_agent = SequenceAgent(self.config)
            annotation_agent = AnnotationAgent(self.config)
            
            logger.info("Stage 2: Launching Sequence agent...")
            sequence_res = await sequence_agent.process(session, signal_cmds)
            
            if sequence_res:
                logger.info("Stage 2.5: Launching Annotation agent...")
                annotated_steps = await annotation_agent.process(session, sequence_res)
                if annotated_steps:
                    sequence_res = annotated_steps
                    
            swarm_results["sequence"] = sequence_res
            with open(agents_cp, "w", encoding="utf-8") as f:
                json.dump(swarm_results, f, indent=2)

        # ── Stage 3: Check for existing Runbook checkpoint ───────────────────
        runbook_cp = self._get_checkpoint_path(session.id, "runbook")
        if runbook_cp.exists():
            logger.info("Found runbook checkpoint for %s, skipping merger phase.", session.id)
            with open(runbook_cp, "r", encoding="utf-8") as f:
                rb_data = json.load(f)
            return Runbook.model_validate(rb_data)
            
        # ── Stage 4: Run sequential Merger phase ─────────────────────────────
        logger.info("Running Merger Agent...")
        merger = MergerAgent(self.config)
        merged_data = await merger.process(
            session=session,
            signal_cmds=swarm_results.get("signal", []),
            sequence_steps=swarm_results.get("sequence", []),
            prereqs=swarm_results.get("prereq", []),
            errors_fixes=swarm_results.get("failure", [])
        )
        
        merged_data["raw_signal_commands"] = swarm_results.get("signal", [])
        merged_data["session_id"] = session.id
        
        runbook = Runbook.model_validate(merged_data)
        
        with open(runbook_cp, "w", encoding="utf-8") as f:
            f.write(runbook.model_dump_json(indent=2))
        logger.info("Saved runbook checkpoint: %s", runbook_cp)
        
        return runbook

    async def _run_parallel_swarm(self, session: Session, events: list[RawEvent]) -> dict[str, Any]:
        """Runs the independent agents in parallel, staggered by 3 seconds."""
        signal_agent = SignalAgent(self.config)
        failure_agent = FailureAgent(self.config)
        prereq_agent = PrereqAgent(self.config)
        
        async def run_signal():
            return await signal_agent.process(session, events)

        async def run_failure():
            await asyncio.sleep(3.0)
            return await failure_agent.process(session, events)

        async def run_prereq():
            await asyncio.sleep(6.0)
            return await prereq_agent.process(session, events)

        logger.info("Stage 1: Launching Signal, Failure, and Prereq agents in parallel...")
        signal_res, failure_res, prereq_res = await asyncio.gather(
            run_signal(),
            run_failure(),
            run_prereq()
        )
        
        return {
            "signal": signal_res,
            "failure": failure_res,
            "prereq": prereq_res
        }
