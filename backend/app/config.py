"""Per-user configuration and storage locations for installed TinyTable.

Standard-library only: ``tinytable-cli channel`` imports this package from the
system Python without the server dependencies.

``config.env`` holds ``KEY=VALUE`` lines in the same shape as ``.env``. Its
values are defaults only; a variable already present in the process
environment always wins, so launch scripts and service units keep control.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def frozen() -> bool:
    """True when running from a packaged (PyInstaller) build."""

    return bool(getattr(sys, "frozen", False))


def config_dir() -> Path:
    if os.name == "nt":
        return Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming") / "TinyTable"
    return Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config") / "tinytable"


def default_db_path() -> Path:
    if os.name == "nt":
        # Program Files is not writable; keep history in the per-user local profile.
        return Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "TinyTable" / "data" / "tinytable.db"
    return Path("data/tinytable.db")


def config_file() -> Path:
    configured = os.getenv("TINYTABLE_CONFIG")
    return Path(configured) if configured else config_dir() / "config.env"


def parse_config(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_config_file(path: Path | None = None) -> dict[str, str]:
    """Apply config-file defaults to ``os.environ``; return the values applied."""

    path = path or config_file()
    if not path.is_file():
        return {}
    applied = {}
    for key, value in parse_config(path.read_text(encoding="utf-8")).items():
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied
