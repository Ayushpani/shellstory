"""
shellstory.utils.ndjson — NDJSON (Newline-Delimited JSON) read/write helpers.

NDJSON is the capture format: one JSON object per line, no outer array.
This module handles malformed lines gracefully — partial writes from shell hooks
are expected during crashes or Ctrl-C.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from shellstory.models import RawEvent

logger = logging.getLogger(__name__)


def load_events(path: Path) -> list[RawEvent]:
    """
    Load all events from an NDJSON capture file.

    Malformed lines are logged and skipped — never fails on bad input.

    Args:
        path: Path to the .ndjson capture file.

    Returns:
        List of parsed RawEvent models, in file order.

    Raises:
        FileNotFoundError: If the capture file doesn't exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Capture file not found: {path}")

    events: list[RawEvent] = []
    skipped = 0

    with open(path, encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
                events.append(RawEvent(**data))
            except json.JSONDecodeError as e:
                logger.warning("Skipping malformed JSON on line %d in %s: %s", line_no, path, e)
                skipped += 1
            except Exception as e:
                logger.warning("Skipping invalid event on line %d in %s: %s", line_no, path, e)
                skipped += 1

    if skipped > 0:
        logger.info("Loaded %d events from %s (%d lines skipped)", len(events), path, skipped)

    return events


def append_event(path: Path, event: dict) -> None:
    """
    Append a single event dict as one NDJSON line.

    Thread-safe at the OS level (single-line atomic writes on most filesystems).
    The caller is responsible for sequence numbering.

    Args:
        path: Path to the .ndjson capture file.
        event: Event dict to serialise and append.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, default=str, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def count_events(path: Path) -> int:
    """Count the number of valid NDJSON lines in a file (for status display)."""
    if not path.exists():
        return 0
    count = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    json.loads(line)
                    count += 1
                except json.JSONDecodeError:
                    pass
    return count


def events_to_ndjson(events: list[RawEvent], path: Path) -> None:
    """Write a list of RawEvent models to an NDJSON file (for testing/export)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(event.model_dump_json() + "\n")
