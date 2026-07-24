"""
Registry for tracking OpenCode instance processes.

Every running instance is one file at:
    ~/.opencode-runtime/servers/<key>.json

Used by both the CLI (opencode-runtime serve/ps/stop) and the library
(OpenCodeRuntime) — the registry is the shared source of truth for all
running instances regardless of how they were started.

This module only persists and locks JSON state. It has no opinion on
whether a pid is actually alive (see process.py) or whether OpenCode is
healthy (see server.py).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Generator

from .exceptions import RegistryBusyError

REGISTRY_DIR = Path(
    os.environ.get("OPENCODE_RUNTIME_REGISTRY_DIR")
    or (Path.home() / ".opencode-runtime" / "servers")
)

# A claim (pid still None) older than this is treated as abandoned — its
# starter crashed (SIGKILL, host reboot) before finishing _start(). Comfortably
# above _wait_healthy's default 60s startup timeout.
_START_LEASE_SECONDS = 90

# A lock file older than this is treated as abandoned — its holder crashed
# mid read-check-write. Locks here are only ever held across a handful of
# filesystem syscalls, so a few seconds of staleness tolerance is generous.
_LOCK_STALE_SECONDS = 5


class ServerState(str, Enum):
    """Server lifecycle state (kept for server.py compatibility)."""

    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass
class RegistryEntry:
    """A server entry in the registry.

    pid is None while the key is claimed but the process hasn't been
    spawned yet. instance_id identifies this process generation, so a
    delete can be scoped to "this generation only" via delete_if_instance().
    pid_start_time guards against pid having been reused by an unrelated
    process — see process.is_same().
    """

    key: str
    state: ServerState
    pid: int | None
    port: int
    password: str
    project_dir: str
    server_dir: str | None
    started_at: str  # ISO-8601
    claimed_at: str  # ISO-8601; only meaningful while state == "starting"
    workspace: str | None = None
    user_id: str | None = None
    runtime_version: str | None = None
    instance_id: str | None = None
    pid_start_time: str | None = None


_FIELD_NAMES = {f.name for f in fields(RegistryEntry)}


def _path(key: str) -> Path:
    return REGISTRY_DIR / f"{key}.json"


def _deserialize(text: str) -> RegistryEntry:
    data = json.loads(text)
    return RegistryEntry(**{k: v for k, v in data.items() if k in _FIELD_NAMES})


@contextmanager
def _locked(key: str, wait_seconds: float = 1.0) -> Generator[None, None, None]:
    """Hold a short-lived exclusive lock on key for a read-check-write step.

    Legitimate holders never keep this past a few syscalls, so genuine
    contention clears in milliseconds — wait_seconds is generous headroom,
    not a real operation budget.
    """
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = REGISTRY_DIR / f"{key}.lock"
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            os.close(os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            break
        except FileExistsError:
            pass
        try:
            stale = time.time() - lock_path.stat().st_mtime > _LOCK_STALE_SECONDS
        except FileNotFoundError:
            stale = False
        if stale:
            lock_path.unlink(missing_ok=True)  # holder crashed; reclaim
            continue
        if time.monotonic() >= deadline:
            raise RegistryBusyError(f"registry entry {key!r} is locked by another operation")
        time.sleep(0.01)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def write(entry: RegistryEntry) -> None:
    """Write (insert or replace) a registry entry."""
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_DIR / f".{entry.key}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(asdict(entry)), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, _path(entry.key))  # atomic — readers never see a partial write


def read(key: str) -> RegistryEntry | None:
    """Read a registry entry by key. Returns None if not found."""
    try:
        return _deserialize(_path(key).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def delete(key: str) -> None:
    """Remove a registry entry. No-op if not found."""
    _path(key).unlink(missing_ok=True)


def list_all() -> list[RegistryEntry]:
    """Return all registry entries."""
    if not REGISTRY_DIR.exists():
        return []
    entries = []
    for path in REGISTRY_DIR.glob("*.json"):
        try:
            entries.append(_deserialize(path.read_text(encoding="utf-8")))
        except (FileNotFoundError, json.JSONDecodeError):
            continue  # deleted mid-scan, or a crash left a partial write
    return entries


def claim_starting(entry: RegistryEntry) -> bool:
    """Claim entry.key for a new start attempt.

    Returns True if this call claimed the key, False if a live claim or a
    running server already occupies it. A claim (pid still None) older
    than _START_LEASE_SECONDS is treated as abandoned and reclaimed.
    """
    with _locked(entry.key):
        existing = read(entry.key)
        if existing is not None:
            if existing.pid is not None:
                return False
            claimed_at = datetime.fromisoformat(existing.claimed_at)
            if datetime.now(timezone.utc) - claimed_at < timedelta(seconds=_START_LEASE_SECONDS):
                return False
        write(entry)
        return True


def delete_if_instance(key: str, instance_id: str | None) -> bool:
    """Delete the entry for key iff its instance_id matches. Returns whether deleted."""
    with _locked(key):
        entry = read(key)
        if entry is None or entry.instance_id != instance_id:
            return False
        delete(key)
        return True


def now_iso() -> str:
    """Return current UTC time as ISO-8601 string with fixed microsecond precision.

    Fixed precision (rather than omitting the fraction when it's exactly
    zero, as isoformat() does by default) keeps these strings correctly
    orderable by plain string comparison, e.g. in claim_starting()'s lease check.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")
