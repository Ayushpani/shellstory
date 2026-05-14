"""
shellstory.db — SQLite storage for sessions and runbooks.

Database location: ~/.shellstory/shellstory.db
Thread-safe via check_same_thread=False (only one writer at a time via SQLite's
built-in locking).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from shellstory.config import SHELLSTORY_DIR
from shellstory.models import Runbook, Session

DB_PATH = SHELLSTORY_DIR / "shellstory.db"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Schema
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    capture_file    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'capturing',
    shell_type      TEXT,
    runbook_id      TEXT,
    error_message   TEXT
);

CREATE TABLE IF NOT EXISTS runbooks (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL,
    data_json   TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_runbooks_session ON runbooks(session_id);
"""

# Migration tracking — add new migrations as the schema evolves
MIGRATIONS = [
    # v1 → v2: add shell_type column (safe to re-run)
    """
    ALTER TABLE sessions ADD COLUMN shell_type TEXT;
    """,
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Database class
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class Database:
    """
    SQLite storage backend.

    Usage:
        db = Database()
        db.create_session(session)
        sessions = db.list_sessions()
    """

    def __init__(self, db_path: Path | None = None):
        path = db_path or DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")  # better concurrent read perf
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._run_migrations()
        self.conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()

    # ── Sessions ─────────────────────────────────────────────────────────────

    def create_session(self, session: Session) -> None:
        """Insert a new capture session."""
        self.conn.execute(
            """INSERT INTO sessions (id, title, started_at, ended_at, capture_file, status, shell_type, runbook_id, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.id,
                session.title,
                session.started_at.isoformat(),
                session.ended_at.isoformat() if session.ended_at else None,
                session.capture_file,
                session.status,
                session.shell_type,
                session.runbook_id,
                session.error_message,
            ),
        )
        self.conn.commit()

    def get_session(self, session_id: str) -> Session | None:
        """Fetch a session by ID (supports prefix matching for convenience)."""
        # Try exact match first
        row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row:
            return _row_to_session(row)

        # Try prefix match (user can type first 8 chars)
        if len(session_id) >= 4:
            row = self.conn.execute(
                "SELECT * FROM sessions WHERE id LIKE ? ORDER BY started_at DESC LIMIT 1",
                (f"{session_id}%",),
            ).fetchone()
            return _row_to_session(row) if row else None

        return None

    def get_active_session(self) -> Session | None:
        """Get the most recent session with status='capturing'."""
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE status = 'capturing' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return _row_to_session(row) if row else None

    def update_session_status(
        self,
        session_id: str,
        status: str,
        ended_at: datetime | None = None,
        error_message: str | None = None,
        runbook_id: str | None = None,
    ) -> None:
        """Update session status and optional fields."""
        self.conn.execute(
            """UPDATE sessions
               SET status = ?, ended_at = ?, error_message = ?, runbook_id = COALESCE(?, runbook_id)
               WHERE id = ?""",
            (
                status,
                ended_at.isoformat() if ended_at else None,
                error_message,
                runbook_id,
                session_id,
            ),
        )
        self.conn.commit()

    def list_sessions(self, limit: int = 50) -> list[Session]:
        """List sessions, most recent first."""
        rows = self.conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_session(r) for r in rows]

    # ── Runbooks ─────────────────────────────────────────────────────────────

    def save_runbook(self, runbook: Runbook) -> None:
        """Save a runbook and link it to its session."""
        self.conn.execute(
            "INSERT OR REPLACE INTO runbooks (id, session_id, title, description, created_at, data_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                runbook.id,
                runbook.session_id,
                runbook.title,
                runbook.description,
                runbook.created_at.isoformat(),
                runbook.model_dump_json(),
            ),
        )
        self.conn.execute(
            "UPDATE sessions SET runbook_id = ?, status = 'complete' WHERE id = ?",
            (runbook.id, runbook.session_id),
        )
        self.conn.commit()

    def get_runbook(self, runbook_id: str) -> Runbook | None:
        """Fetch a runbook by ID."""
        row = self.conn.execute("SELECT data_json FROM runbooks WHERE id = ?", (runbook_id,)).fetchone()
        if not row:
            return None
        return Runbook.model_validate_json(row["data_json"])

    def get_runbook_by_session(self, session_id: str) -> Runbook | None:
        """Fetch the runbook for a given session."""
        row = self.conn.execute(
            "SELECT data_json FROM runbooks WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return Runbook.model_validate_json(row["data_json"])

    # ── Internal ─────────────────────────────────────────────────────────────

    def _run_migrations(self) -> None:
        """Run schema migrations that haven't been applied yet."""
        for migration in MIGRATIONS:
            try:
                self.conn.executescript(migration)
            except sqlite3.OperationalError:
                # Column/index already exists — safe to skip
                pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _row_to_session(row: sqlite3.Row) -> Session:
    """Convert a database row to a Session model."""
    return Session(
        id=row["id"],
        title=row["title"],
        started_at=datetime.fromisoformat(row["started_at"]),
        ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
        capture_file=row["capture_file"],
        status=row["status"],
        shell_type=row["shell_type"] if "shell_type" in row.keys() else None,
        runbook_id=row["runbook_id"],
        error_message=row["error_message"],
    )
