"""
Registry for tracking opencode server processes.

Each running server is represented by a JSON file at:
    ~/.opencode-harness/servers/<key>.json

Used by both the CLI (opencode-harness serve/ps/stop) and the library
(OpenCodeHarness) — the registry is the shared source of truth for all
running servers regardless of how they were started.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_DIR = Path.home() / ".opencode-harness" / "servers"


@dataclass
class RegistryEntry:
    key: str
    pid: int
    port: int
    password: str
    project_dir: str
    server_dir: str | None
    started_at: str  # ISO-8601
    workspace: str | None = None
    user_id: str | None = None


def write(entry: RegistryEntry) -> None:
    """Write a registry entry to disk. File is chmod 0o600."""
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    path = REGISTRY_DIR / f"{entry.key}.json"
    path.write_text(json.dumps(asdict(entry), indent=2), encoding="utf-8")
    path.chmod(0o600)


def read(key: str) -> RegistryEntry | None:
    """Read a registry entry by key. Returns None if not found."""
    path = REGISTRY_DIR / f"{key}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return RegistryEntry(**data)


def delete(key: str) -> None:
    """Remove a registry entry. No-op if not found."""
    path = REGISTRY_DIR / f"{key}.json"
    path.unlink(missing_ok=True)


def list_all() -> list[RegistryEntry]:
    """Return all registry entries on disk."""
    if not REGISTRY_DIR.exists():
        return []
    entries = []
    for path in REGISTRY_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries.append(RegistryEntry(**data))
        except Exception:
            pass
    return entries


def is_alive(pid: int) -> bool:
    """Return True if a process with this PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
