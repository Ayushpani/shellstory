"""
shellstory.daemon — Background processing daemon.

Tails the NDJSON capture file and processes events in rolling batches
to provide real-time analysis without blowing through API rate limits.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from shellstory.config import get_sessions_dir
from shellstory.models import Session, RawEvent
from shellstory.utils.ndjson import load_events
from shellstory.agents.specialists import SignalAgent, FailureAgent, PrereqAgent
from shellstory.redact import PIIScannerAgent, redact_events

logger = logging.getLogger(__name__)

BATCH_INTERVAL = 30.0  # seconds between waking up to check for new events


class SessionDaemon:
    """Background daemon to incrementally process session events."""

    def __init__(self, session: Session, config: dict[str, Any]):
        self.session = session
        self.config = config
        self.capture_path = Path(session.capture_file)
        self.sessions_dir = get_sessions_dir(config)
        self.state_file = self.sessions_dir / f"{session.id}.state.json"
        self.processed_count = 0
        
        # Agents
        self.signal_agent = SignalAgent(config)
        self.failure_agent = FailureAgent(config)
        self.prereq_agent = PrereqAgent(config)
        self.pii_scanner = PIIScannerAgent(config)
        
        # State
        self.state: dict[str, Any] = {
            "signal_commands": [],
            "errors_and_fixes": [],
            "prerequisites": [],
            "pii_secrets": [],
        }
        self._load_state()

    def _load_state(self) -> None:
        """Load existing state if restarting."""
        if self.state_file.exists():
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    data = json.load(f)
                    self.state.update(data)
                    self.processed_count = data.get("_processed_count", 0)
            except Exception as e:
                logger.warning(f"Failed to load daemon state: {e}")

    def _save_state(self) -> None:
        """Persist state to disk."""
        self.state["_processed_count"] = self.processed_count
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    async def run(self) -> None:
        """Main daemon loop."""
        logger.info(f"Daemon started for session {self.session.id[:8]}")
        
        while True:
            # Check if session is still active
            # For a real daemon, we might check the database or a pid file.
            # Here we just read the file until a 'session_end' event appears
            # or the user stops it via the CLI sending a signal.
            
            try:
                events = load_events(self.capture_path)
            except FileNotFoundError:
                events = []
                
            new_events = events[self.processed_count:]
            
            if new_events:
                await self._process_batch(events, new_events)
                self.processed_count = len(events)
                self._save_state()
                
            # If session ended naturally
            if any(e.event_type == "session_end" for e in new_events):
                logger.info("Session end event detected. Daemon exiting.")
                break
                
            await asyncio.sleep(BATCH_INTERVAL)

    async def _process_batch(self, all_events: list[RawEvent], new_events: list[RawEvent]) -> None:
        """Process a batch of new events."""
        logger.info(f"Processing batch of {len(new_events)} new events...")
        
        # 1. Redaction (Regex is fast, done on all new events immediately)
        # We don't save regex state here, that's done in the final process step,
        # but we run the AI PII scanner on the new batch.
        ai_secrets = await self.pii_scanner.process(self.session, new_events)
        if ai_secrets:
            self.state["pii_secrets"].extend(ai_secrets)
            
        # 2. Extract Commands
        new_cmds = [e for e in new_events if e.event_type == "command" and e.command]
        if not new_cmds:
            return
            
        # 3. Parallel Background Analysis
        tasks = [
            self.signal_agent.process(self.session, new_cmds),
            self.failure_agent.process(self.session, new_events), # Give context
            self.prereq_agent.process(self.session, new_cmds)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Merge results safely
        if not isinstance(results[0], Exception):
            self.state["signal_commands"].extend(results[0])
            
        if not isinstance(results[1], Exception):
            self.state["errors_and_fixes"].extend(results[1])
            
        if not isinstance(results[2], Exception):
            # Prereqs are strings, avoid duplicates
            current_prereqs = set(self.state["prerequisites"])
            for p in results[2]:
                if p not in current_prereqs:
                    self.state["prerequisites"].append(p)


def run_daemon_sync(session_json: str, config_json: str) -> None:
    """Entry point for the background process."""
    # Setup basic logging for the detached process
    from shellstory.config import get_sessions_dir
    config = json.loads(config_json)
    sessions_dir = get_sessions_dir(config)
    
    # We log to a daemon.log file instead of stdout
    log_file = sessions_dir / "daemon.log"
    logging.basicConfig(
        filename=str(log_file),
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )
    
    session_dict = json.loads(session_json)
    session = Session(**session_dict)
    
    daemon = SessionDaemon(session, config)
    
    try:
        asyncio.run(daemon.run())
    except Exception as e:
        logger.error(f"Daemon crashed: {e}", exc_info=True)
