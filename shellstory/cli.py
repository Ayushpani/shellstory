"""
shellstory.cli — Command-line interface.

Commands:
  shellstory configure        Interactive setup wizard
  shellstory start [TITLE]    Start a capture session
  shellstory stop             Stop the active capture session
  shellstory process [ID]     Process a session into a runbook
  shellstory list             List all sessions
  shellstory export [ID]      Export a runbook to the configured connector
  shellstory status           Show the active session status
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from shellstory import __version__
from shellstory.config import (
    CONFIG_PATH,
    DEFAULT_CONFIG,
    SHELLSTORY_DIR,
    ensure_dirs,
    get_sessions_dir,
    load_config,
    save_config,
)
from shellstory.db import Database
from shellstory.models import Session

console = Console()
err_console = Console(stderr=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Logging setup
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main group
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@click.group()
@click.version_option(version=__version__, prog_name="shellstory")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """ShellStory -- Turn shell sessions into production runbooks."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# configure
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.command()
def configure() -> None:
    """Interactive setup wizard for ShellStory."""
    ensure_dirs()

    console.print(Panel.fit(
        "[bold cyan]ShellStory Configuration Wizard[/bold cyan]\n"
        f"Config location: {CONFIG_PATH}",
        border_style="cyan",
    ))

    # Load existing config or use defaults
    try:
        config = load_config()
        console.print("[dim]Loaded existing config.[/dim]")
    except (FileNotFoundError, ValueError):
        config = dict(DEFAULT_CONFIG)

    # API Key
    current_key = config.get("llm", {}).get("api_key", "")
    display_key = f"{current_key[:8]}...{current_key[-4:]}" if len(current_key) > 12 else "(not set)"

    api_key = click.prompt(
        f"OpenRouter API key [{display_key}]",
        default=current_key or "",
        show_default=False,
    )
    if api_key:
        config.setdefault("llm", {})["api_key"] = api_key

    # Provider
    provider = click.prompt(
        "LLM provider",
        default=config.get("llm", {}).get("provider", "openrouter"),
        type=click.Choice(["openrouter", "nvidia"]),
    )
    config["llm"]["provider"] = provider

    # Model (for single-model operations, the swarm uses its own chains)
    model = click.prompt(
        "Default model (for non-swarm operations)",
        default=config.get("llm", {}).get("model", "anthropic/claude-sonnet-4"),
    )
    config["llm"]["model"] = model

    # Output connector
    connector = click.prompt(
        "Default export format",
        default=config.get("default_connector", "markdown"),
        type=click.Choice(["markdown"]),
    )
    config["default_connector"] = connector

    # Output dir
    md_dir = click.prompt(
        "Markdown output directory",
        default=config.get("connectors", {}).get("markdown", {}).get("output_dir", "~/runbooks"),
    )
    config.setdefault("connectors", {}).setdefault("markdown", {})["output_dir"] = md_dir

    # Save
    path = save_config(config)
    console.print(f"\n[green]Config saved to {path}[/green]")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# start
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.command()
@click.argument("title", default="Untitled Session")
@click.option("--shell", type=click.Choice(["powershell", "bash", "zsh", "auto"]), default="auto")
def start(title: str, shell: str) -> None:
    """Start a new capture session."""
    from shellstory.capture import create_hook_file, detect_shell, write_session_start_event

    ensure_dirs()

    # Check for existing active session
    db = Database()
    active = db.get_active_session()
    if active:
        err_console.print(
            f"[yellow]Warning: Session '{active.title}' ({active.id[:8]}) is already active.[/yellow]"
        )
        if not click.confirm("Start a new session anyway?"):
            return
        db.update_session_status(active.id, "error", error_message="Superseded by new session")

    # Detect shell
    shell_type = shell if shell != "auto" else detect_shell()

    # Generate IDs and paths
    session_id = str(uuid.uuid4())
    try:
        config = load_config()
    except (FileNotFoundError, ValueError):
        config = DEFAULT_CONFIG

    sessions_dir = get_sessions_dir(config)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    capture_file = sessions_dir / f"{session_id}.ndjson"

    # Create session record
    session = Session(
        id=session_id,
        title=title,
        started_at=datetime.now(timezone.utc),
        capture_file=str(capture_file),
        status="capturing",
        shell_type=shell_type,
    )
    db.create_session(session)

    # Generate hook script
    hook_path = create_hook_file(
        session_id=session_id,
        capture_file=str(capture_file),
        shell_type=shell_type,
        sessions_dir=sessions_dir,
    )
    
    # Initialize the capture file immediately
    write_session_start_event(capture_file, session_id, shell_type)

    # Spawn daemon in the background
    import subprocess
    import sys
    
    cmd = [sys.executable, "-m", "shellstory.cli", "daemon", session_id]
    if platform.system() == "Windows":
        subprocess.Popen(cmd, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        subprocess.Popen(cmd, start_new_session=True)

    console.print(Panel.fit(
        f"[bold green]Session Started[/bold green]\n\n"
        f"  [bold]ID:[/bold]    {session_id}\n"
        f"  [bold]Title:[/bold] {title}\n"
        f"  [bold]Shell:[/bold] {shell_type}\n"
        f"  [bold]Log:[/bold]   {capture_file}\n\n"
        f"Dropping you into a recording shell now. Type [bold cyan]exit[/bold cyan] when done.",
        border_style="green"
    ))
    
    # Auto-activate the shell hook so the user doesn't have to copy-paste it
    if shell_type == "powershell":
        subprocess.run(["powershell", "-NoExit", "-Command", f". '{hook_path}'"])
    elif shell_type == "bash":
        subprocess.run(["bash", "--rcfile", str(hook_path)])
    elif shell_type == "zsh":
        # Zsh uses ZDOTDIR to override rc files
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as d:
            with open(Path(d) / ".zshrc", "w") as f:
                f.write(f"source ~/.zshrc\nsource '{hook_path}'\n")
            env = os.environ.copy()
            env["ZDOTDIR"] = d
            subprocess.run(["zsh"], env=env)
            
    # When the user exits the subshell, the session is over
    db = Database()
    db.update_session_status(session_id, "processing")
    db.close()
    
    console.print(Panel.fit(
        f"[bold green]Session Stopped[/bold green]\n\n"
        f"  [bold]ID:[/bold]     {session_id}\n"
        f"  [bold]Title:[/bold]  {title}\n\n"
        f"Next: Run [bold cyan]shellstory process {session_id[:8]}[/bold cyan] to generate the runbook.",
        border_style="green"
    ))
    
    # We no longer need the explicit `stop` command since `exit` handles it,
    # but we'll leave it in the CLI for backwards compatibility.
    db.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# stop
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.command()
def stop() -> None:
    """Stop the active capture session."""
    from shellstory.utils.ndjson import count_events

    db = Database()
    session = db.get_active_session()

    if not session:
        err_console.print("[yellow]No active session found.[/yellow]")
        db.close()
        return

    # Count captured events
    capture_path = Path(session.capture_file)
    event_count = count_events(capture_path) if capture_path.exists() else 0

    # Update session
    db.update_session_status(
        session.id,
        status="processing",
        ended_at=datetime.now(timezone.utc),
    )

    console.print(Panel.fit(
        f"[bold green]Session Stopped[/bold green]\n\n"
        f"  ID:     [cyan]{session.id[:8]}...[/cyan]\n"
        f"  Title:  {session.title}\n"
        f"  Events: {event_count}\n\n"
        f"[bold]Next: Run 'shellstory process {session.id[:8]}' to generate the runbook.[/bold]",
        border_style="green",
    ))
    db.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# process
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.command()
@click.argument("session_id", required=False)
@click.option("--skip-validation", is_flag=True, help="Skip the validation/repair loop")
@click.option("--skip-ai-redaction", is_flag=True, help="Use regex-only PII redaction")
def process(session_id: Optional[str], skip_validation: bool, skip_ai_redaction: bool) -> None:
    """Process a captured session into a runbook."""
    db = Database()

    # Find the session
    if session_id:
        session = db.get_session(session_id)
    else:
        # Find most recent non-capturing session
        sessions = db.list_sessions(limit=5)
        session = next(
            (s for s in sessions if s.status in ("processing", "error")), None
        )

    if not session:
        err_console.print("[red]No session found. Run 'shellstory start' first.[/red]")
        db.close()
        return

    if session.status == "complete":
        console.print(f"[yellow]Session {session.id[:8]} is already complete.[/yellow]")
        if not click.confirm("Re-process?"):
            db.close()
            return

    try:
        config = load_config()
    except (FileNotFoundError, ValueError) as e:
        err_console.print(f"[red]Config error: {e}[/red]")
        db.close()
        return

    from shellstory.ui import SwarmDashboard
    from rich.live import Live

    class DashboardLogHandler(logging.Handler):
        def __init__(self, dash: SwarmDashboard):
            super().__init__()
            self.dash = dash
            
        def emit(self, record: logging.LogRecord) -> None:
            try:
                msg = self.format(record)
                self.dash.log(msg)
            except Exception:
                pass

    dashboard = SwarmDashboard(session.id)
    
    root_logger = logging.getLogger("shellstory")
    # Remove existing handlers to stop terminal scrolling
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        
    dash_handler = DashboardLogHandler(dashboard)
    dash_handler.setFormatter(logging.Formatter('%(message)s'))
    root_logger.addHandler(dash_handler)
    root_logger.setLevel(logging.INFO)

    try:
        with Live(dashboard, refresh_per_second=15, console=console) as live:
            asyncio.run(_process_async(session, config, db, skip_validation, skip_ai_redaction, dashboard))
    except Exception as e:
        console.print_exception()
        err_console.print(f"\n[red]Fatal error during processing: {e}[/red]")
        logger.exception("Processing failed")


async def _process_async(
    session: Session,
    config: dict,
    db: Database,
    skip_validation: bool,
    skip_ai_redaction: bool,
    dashboard: "SwarmDashboard",
) -> None:
    """Async processing pipeline."""
    from shellstory.agents.swarm import SwarmOrchestrator
    from shellstory.connectors import get_connector
    from shellstory.redact import PIIScannerAgent, redact_events
    from shellstory.utils.ndjson import load_events
    from shellstory.validate import validate_and_repair

    capture_path = Path(session.capture_file)
    sessions_dir = get_sessions_dir(config)

    # ── Stage 1: Load events ─────────────────────────────────────────────────
    dashboard.update_stage("ingest", "active", "Loading events...")
    try:
        events = load_events(capture_path)
    except FileNotFoundError:
        events = []

    if not events:
        dashboard.update_stage("ingest", "error", "No events found")
        db.update_session_status(session.id, "error", error_message="Empty or missing capture file")
        db.close()
        return

    dashboard.update_stage("ingest", "completed", f"Loaded {len(events)} events")

    # ── Stage 2: PII Redaction ───────────────────────────────────────────────
    dashboard.update_stage("pii", "active", "Regex scanning...")
    redaction_cp = sessions_dir / f"{session.id}.redacted.json"

    if redaction_cp.exists():
        with open(redaction_cp) as f:
            import shellstory.models as m
            rd = json.load(f)
            redaction_result = m.RedactionResult.model_validate(rd)
        dashboard.update_stage("pii", "completed", f"Loaded checkpoint")
    else:
        redaction_result = redact_events(events)
        
        if not skip_ai_redaction:
            dashboard.update_stage("pii", "active", "AI scanning...")
            try:
                ai_scanner = PIIScannerAgent(config)
                ai_findings = await ai_scanner.process(session, events)
            except Exception as e:
                pass

        with open(redaction_cp, "w") as f:
            f.write(redaction_result.model_dump_json(indent=2))
        dashboard.update_stage("pii", "completed", f"Scanned {len(events)} events")

    # CRITICAL: Apply redactions to the event stream before passing to agents.
    # Without this, the raw secrets would leak into the LLM prompts and final output.
    if redaction_result.events:
        from shellstory.models import RawEvent
        events = [
            RawEvent(
                sequence=getattr(rev, 'sequence', getattr(rev, 'original_sequence', 0)),
                event_type=rev.event_type,
                timestamp=rev.timestamp,
                command=rev.command,
                working_dir=rev.working_dir,
                exit_code=rev.exit_code,
                duration_ms=rev.duration_ms,
                stream=rev.stream,
                text=rev.text,
                shell=rev.shell,
                os=rev.os,
            )
            for rev in redaction_result.events
        ]

    # ── Stage 3: Agent Swarm ─────────────────────────────────────────────────
    dashboard.update_stage("swarm", "active", "Running...")
    orchestrator = SwarmOrchestrator(config, dashboard=dashboard)
    runbook = await orchestrator.run(session, events)
    dashboard.update_stage("swarm", "completed", f"Generated {len(runbook.steps)} steps")

    # ── Stage 4: Validation ──────────────────────────────────────────────────
    if not skip_validation:
        dashboard.update_stage("validate", "active", "Analyzing...")
        try:
            runbook = await validate_and_repair(session, runbook, config)
            dashboard.update_stage("validate", "completed", "Passed")
        except Exception as e:
            dashboard.update_stage("validate", "error", "Skipped")
    else:
        dashboard.update_stage("validate", "completed", "Skipped")

    # ── Stage 5: Save & Export ───────────────────────────────────────────────
    dashboard.update_stage("export", "active", "Saving...")
    db.save_runbook(runbook)

    # Export
    connector_name = config.get("default_connector", "markdown")
    connector = get_connector(connector_name)
    output_path = connector.export(runbook, config)

    db.close()
    dashboard.update_stage("export", "completed", f"Saved to {Path(output_path).name}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# list
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.command(name="list")
@click.option("--limit", "-n", default=20, help="Max sessions to show")
def list_sessions(limit: int) -> None:
    """List all capture sessions."""
    db = Database()
    sessions = db.list_sessions(limit=limit)
    db.close()

    if not sessions:
        console.print("[dim]No sessions found. Run 'shellstory start' to begin.[/dim]")
        return

    table = Table(title="ShellStory Sessions", show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim", width=10)
    table.add_column("Title", min_width=20)
    table.add_column("Status", width=12)
    table.add_column("Shell", width=12)
    table.add_column("Started", width=20)

    status_colors = {
        "capturing": "green",
        "processing": "yellow",
        "complete": "cyan",
        "error": "red",
    }

    for s in sessions:
        color = status_colors.get(s.status, "white")
        table.add_row(
            s.id[:8] + "...",
            s.title,
            f"[{color}]{s.status}[/{color}]",
            s.shell_type or "unknown",
            s.started_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# export
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.command()
@click.argument("session_id")
@click.option("--format", "fmt", default=None, help="Export format (default: from config)")
def export(session_id: str, fmt: Optional[str]) -> None:
    """Export a completed runbook."""
    from shellstory.connectors import get_connector

    db = Database()
    session = db.get_session(session_id)

    if not session:
        err_console.print(f"[red]Session '{session_id}' not found.[/red]")
        db.close()
        return

    runbook = db.get_runbook_by_session(session.id)
    if not runbook:
        err_console.print(f"[red]No runbook found for session '{session_id}'. Run 'shellstory process' first.[/red]")
        db.close()
        return

    try:
        config = load_config()
    except (FileNotFoundError, ValueError):
        config = DEFAULT_CONFIG

    connector_name = fmt or config.get("default_connector", "markdown")
    connector = get_connector(connector_name)
    output_path = connector.export(runbook, config)

    console.print(f"[green]Exported to: {output_path}[/green]")
    db.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# status
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.command()
def status() -> None:
    """Show the current active session status."""
    from shellstory.utils.ndjson import count_events

    db = Database()
    session = db.get_active_session()
    db.close()

    if not session:
        console.print("[dim]No active session. Run 'shellstory start' to begin.[/dim]")
        return

    capture_path = Path(session.capture_file)
    event_count = count_events(capture_path) if capture_path.exists() else 0

    elapsed = datetime.now(timezone.utc) - session.started_at
    elapsed_str = str(elapsed).split(".")[0]  # HH:MM:SS

    console.print(Panel.fit(
        f"[bold green]Active Session[/bold green]\n\n"
        f"  ID:      [cyan]{session.id[:8]}...[/cyan]\n"
        f"  Title:   {session.title}\n"
        f"  Shell:   {session.shell_type or 'unknown'}\n"
        f"  Events:  [cyan]{event_count}[/cyan]\n"
        f"  Elapsed: {elapsed_str}",
        border_style="green",
    ))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# daemon
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@main.command(hidden=True)
@click.argument("session_id")
def daemon(session_id: str) -> None:
    """Internal command to run the background processing daemon."""
    from shellstory.daemon import run_daemon_sync
    
    db = Database()
    session = db.get_session(session_id)
    db.close()
    
    if not session:
        return
        
    try:
        config = load_config()
    except Exception:
        config = DEFAULT_CONFIG
        
    # Serialize for the sync runner
    session_json = session.model_dump_json()
    config_json = json.dumps(config)
    
    run_daemon_sync(session_json, config_json)


if __name__ == "__main__":
    main()
