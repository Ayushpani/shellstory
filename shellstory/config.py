"""
shellstory.config — Configuration loading, validation, and writing.

Config file: ~/.shellstory/config.yaml
All settings are validated on load; missing required fields raise ValueError
with clear messages pointing to `shellstory configure`.
"""

from __future__ import annotations

import os
import platform
import stat
from pathlib import Path
from typing import Any

import yaml

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Paths
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SHELLSTORY_DIR = Path.home() / ".shellstory"
CONFIG_PATH = SHELLSTORY_DIR / "config.yaml"
SESSIONS_DIR = SHELLSTORY_DIR / "sessions"
LOG_FILE = SHELLSTORY_DIR / "shellstory.log"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Defaults
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "openrouter",
        "api_key": "",
        "model": "anthropic/claude-sonnet-4",
        "site_url": "",
        "app_name": "shellstory",
        "model_overrides": {},  # Format: {"agent_name": "model_id"}
    },
    "default_connector": "markdown",
    "sessions_dir": str(SESSIONS_DIR),
    "connectors": {
        "notion": {
            "token": "",
            "parent_page_id": "",
        },
        "obsidian": {
            "vault_path": "~/Documents/MyVault",
            "subfolder": "runbooks",
        },
        "confluence": {
            "base_url": "",
            "username": "",
            "api_token": "",
            "space_key": "",
            "parent_page_id": None,
        },
        "markdown": {
            "output_dir": "~/runbooks",
            "git_commit": False,
            "git_remote": None,
        },
    },
}

# Fields that MUST be present and non-empty for the tool to function
REQUIRED_FIELDS: dict[str, list[str] | None] = {
    "llm.provider": ["openrouter", "nvidia"],
    "llm.api_key": None,  # any non-empty string
    "llm.model": None,
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Public API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """
    Load and validate the shellstory configuration.

    Resolution order:
      1. Explicit path argument
      2. SHELLSTORY_CONFIG environment variable
      3. ~/.shellstory/config.yaml

    Raises:
        FileNotFoundError: Config file doesn't exist.
        ValueError: Required fields missing or invalid.
    """
    path = _resolve_config_path(config_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Config not found at {path}.\n"
            f"Run 'shellstory configure' to set up, or copy .shellstory.example.yaml to {path}"
        )

    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    # Merge with defaults so optional fields always exist
    merged = _deep_merge(DEFAULT_CONFIG, config)
    _validate(merged)

    # Resolve paths
    merged["sessions_dir"] = str(Path(merged["sessions_dir"]).expanduser())

    return merged


def save_config(config: dict[str, Any], config_path: Path | None = None) -> Path:
    """
    Write config to YAML file with secure permissions.

    Returns the path written to.
    """
    path = _resolve_config_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Secure the file — it contains API keys
    _secure_file(path)

    return path


def ensure_dirs() -> None:
    """Create all required directories if they don't exist."""
    SHELLSTORY_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def get_sessions_dir(config: dict[str, Any] | None = None) -> Path:
    """Get the sessions directory from config or default."""
    if config and "sessions_dir" in config:
        return Path(config["sessions_dir"]).expanduser()
    return SESSIONS_DIR


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Internal
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _resolve_config_path(explicit: Path | None) -> Path:
    """Resolve config path with fallback chain."""
    if explicit:
        return Path(explicit)
    env_path = os.environ.get("SHELLSTORY_CONFIG")
    if env_path:
        return Path(env_path)
    return CONFIG_PATH


def _validate(config: dict[str, Any]) -> None:
    """Validate required fields exist and have allowed values."""
    for dotted_key, allowed_values in REQUIRED_FIELDS.items():
        parts = dotted_key.split(".")
        val: Any = config
        for part in parts:
            if not isinstance(val, dict) or part not in val:
                raise ValueError(
                    f"Missing required config field: {dotted_key}\n"
                    f"Run 'shellstory configure' to set it up."
                )
            val = val[part]

        if allowed_values is not None and val not in allowed_values:
            raise ValueError(
                f"Config '{dotted_key}' must be one of {allowed_values}, got '{val}'.\n"
                f"Run 'shellstory configure' to fix."
            )

        if not val:
            raise ValueError(
                f"Config '{dotted_key}' must not be empty.\n"
                f"Run 'shellstory configure' to set it."
            )


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merge override into base.
    Override values take precedence; base provides defaults for missing keys.
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _secure_file(path: Path) -> None:
    """Set file permissions to owner-only (chmod 600) on Unix systems."""
    if platform.system() != "Windows":
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass  # Best effort — some filesystems don't support chmod
