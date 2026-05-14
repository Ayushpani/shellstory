"""
shellstory.ui — Majestic CLI Dashboard for ShellStory.
Zero emojis. Futuristic terminal animation using Rich Layouts.
"""

from typing import Any, Dict, List
import logging
from collections import deque
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.spinner import Spinner

logger = logging.getLogger(__name__)

class SwarmDashboard:
    """Manages the state and layout of the cyber-deck Swarm animation."""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        
        # State
        self.pipeline_stages = [
            {"id": "ingest", "label": "INGESTION", "status": "pending", "desc": "STANDBY"},
            {"id": "pii", "label": "PII REDACTION", "status": "pending", "desc": "STANDBY"},
            {"id": "swarm", "label": "SWARM MATRIX", "status": "pending", "desc": "STANDBY"},
            {"id": "validate", "label": "VALIDATION", "status": "pending", "desc": "STANDBY"},
            {"id": "export", "label": "COMPILATION", "status": "pending", "desc": "STANDBY"},
        ]
        
        self.agents = {
            "signal": {"label": "SIGNAL", "status": "standby", "model": ""},
            "prereq": {"label": "PREREQ", "status": "standby", "model": ""},
            "failure": {"label": "FAILURE", "status": "standby", "model": ""},
            "sequence": {"label": "SEQUENCE", "status": "standby", "model": ""},
            "merger": {"label": "MERGER", "status": "standby", "model": ""},
            "critical_path": {"label": "CRITIC", "status": "standby", "model": ""}
        }
        
        self.logs: deque[str] = deque(maxlen=20)
        
        self.layout = Layout()
        self._init_layout()
        
    def _init_layout(self) -> None:
        self.layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body")
        )
        self.layout["body"].split_row(
            Layout(name="pipeline", ratio=1),
            Layout(name="matrix", ratio=2),
            Layout(name="logs", ratio=2)
        )
        
    def update_stage(self, stage_id: str, status: str, desc: str = "") -> None:
        """status: pending, active, completed, error"""
        for s in self.pipeline_stages:
            if s["id"] == stage_id:
                s["status"] = status
                if desc:
                    s["desc"] = desc
                    
    def update_agent(self, agent_id: str, status: str, model: str = "") -> None:
        """status: standby, running, completed, error, fallback"""
        if agent_id in self.agents:
            self.agents[agent_id]["status"] = status
            if model:
                self.agents[agent_id]["model"] = model
                
    def log(self, message: str) -> None:
        self.logs.append(message)
        
        # Auto-parse log lines to update agent status dynamically
        import re
        
        # Match: [signal] Calling google/gemma-4-31b-it:free (fallback 1/4)
        match = re.search(r"\[(.*?)\] Calling (.*?)(?: \((fallback.*?)\))?$", message)
        if match:
            agent_id = match.group(1).lower()
            model = match.group(2)
            is_fallback = bool(match.group(3))
            
            if agent_id in self.agents:
                status = "fallback" if is_fallback else "running"
                self.update_agent(agent_id, status, model)
                
        # Match completion or checkpoint saving to mark agents complete
        if "Validation passed" in message:
            self.update_agent("critical_path", "completed")
        elif "Runbook assembled" in message:
            for ag in ["signal", "failure", "prereq", "sequence", "merger"]:
                if self.agents[ag]["status"] in ("running", "fallback"):
                    self.update_agent(ag, "completed")
        
    def render_header(self) -> Panel:
        grid = Table.grid(expand=True)
        grid.add_column(justify="left")
        grid.add_column(justify="right")
        grid.add_row(
            Text(">>> SHELLSTORY ORCHESTRATOR", style="bold cyan"),
            Text(f"SESSION: {self.session_id}", style="dim")
        )
        return Panel(grid, border_style="cyan")
        
    def render_pipeline(self) -> Panel:
        table = Table.grid(padding=(0, 2), expand=True)
        table.add_column(justify="left", width=3)
        table.add_column(justify="left")
        
        for i, stage in enumerate(self.pipeline_stages, 1):
            status = stage["status"]
            if status == "pending":
                icon = Text(".", style="dim")
                color = "dim"
            elif status == "active":
                icon = Spinner("dots", style="cyan")
                color = "bold cyan"
            elif status == "completed":
                icon = Text("+", style="green")
                color = "bold green"
            else:
                icon = Text("!", style="red")
                color = "bold red"
                
            table.add_row(
                icon,
                Text(f"{i}. {stage['label']}", style=color),
            )
            # Add desc row underneath
            table.add_row("", Text(stage["desc"], style="dim"))
            table.add_row("", "") # spacing
            
        return Panel(table, title="[bold cyan]PIPELINE", border_style="cyan")
        
    def render_matrix(self) -> Panel:
        table = Table.grid(padding=(0, 2), expand=True)
        table.add_column(justify="left", width=12)
        table.add_column(justify="center", width=3)
        table.add_column(justify="left")
        
        for agent_id, data in self.agents.items():
            status = data["status"]
            if status == "standby":
                icon = Text(".", style="dim")
                state = Text("STANDBY", style="dim")
            elif status == "running":
                icon = Spinner("bouncingBar", style="magenta")
                model_name = data["model"].split("/")[-1] if data["model"] else "RUNNING"
                state = Text(model_name, style="bold magenta")
            elif status == "fallback":
                icon = Spinner("bouncingBar", style="yellow")
                model_name = data["model"].split("/")[-1] if data["model"] else "FALLBACK"
                state = Text(f"REROUTE: {model_name}", style="bold yellow")
            elif status == "completed":
                icon = Text("+", style="green")
                state = Text("COMPLETED", style="bold green")
            else:
                icon = Text("!", style="red")
                state = Text("ERROR", style="bold red")
                
            table.add_row(Text(data["label"], style="bold white"), icon, state)
            table.add_row("", "", "") # spacing
            
        return Panel(table, title="[bold cyan]SWARM THREADS", border_style="cyan")
        
    def render_logs(self) -> Panel:
        log_text = Text()
        for msg in self.logs:
            log_text.append(f"> {msg}\n", style="dim")
        return Panel(log_text, title="[bold cyan]SYSTEM LOG", border_style="cyan")
        
    def __rich__(self) -> Layout:
        """Called by rich.live to render the object."""
        self.layout["header"].update(self.render_header())
        self.layout["pipeline"].update(self.render_pipeline())
        self.layout["matrix"].update(self.render_matrix())
        self.layout["logs"].update(self.render_logs())
        return self.layout

