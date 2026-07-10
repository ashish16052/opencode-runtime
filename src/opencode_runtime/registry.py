"""
Registry for tracking OpenCode instance processes.

Every running instance is a row in a SQLite database at:
    ~/.opencode-runtime/servers/registry.db

Used by both the CLI (opencode-runtime serve/ps/stop) and the library
(OpenCodeRuntime) — the registry is the shared source of truth for all
running instances regardless of how they were started.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Generator

REGISTRY_DIR = Path(
    os.environ.get("OPENCODE_RUNTIME_REGISTRY_DIR")
    or (Path.home() / ".opencode-runtime" / "servers")
)

# A 'starting' row older than this is treated as abandoned (its starter
# crashed — SIGKILL, host reboot — before reaching write()/delete()) and is
# reclaimed by the next claim_starting() call. Comfortably above
# _wait_healthy's default 60s startup timeout, so a claim only expires once
# a start attempt has definitely either finished or died without cleaning
# up after itself.
_START_LEASE_SECONDS = 90


class ServerState(str, Enum):
    """Server lifecycle state.

    STARTING: claimed startup slot, awaiting health check.
    RUNNING: process alive, health check passing.
    STOPPING: shutdown initiated (reserved for future use).
    FAILED: startup failed or lease expired (reserved for future use).
    """

    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass
class RegistryEntry:
    """A server entry in the registry.

    state: ServerState enum value. Display status is derived from state + observed health.
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
    last_used_at: str | None = None
    runtime_version: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS servers (
    key             TEXT PRIMARY KEY,
    state           TEXT NOT NULL,
    pid             INTEGER,
    port            INTEGER NOT NULL,
    password        TEXT NOT NULL,
    project_dir     TEXT NOT NULL,
    server_dir      TEXT,
    started_at      TEXT NOT NULL,
    claimed_at      TEXT NOT NULL,
    workspace       TEXT,
    user_id         TEXT,
    last_used_at    TEXT,
    runtime_version TEXT
)
"""

_INSERT = """
INSERT INTO servers (key, state, pid, port, password, project_dir, server_dir,
                     started_at, claimed_at, workspace, user_id, last_used_at,
                     runtime_version)
VALUES (:key, :state, :pid, :port, :password, :project_dir, :server_dir,
        :started_at, :claimed_at, :workspace, :user_id, :last_used_at,
        :runtime_version)
"""

_UPSERT = _INSERT.replace("INSERT", "INSERT OR REPLACE", 1)


@contextmanager
def _connect() -> Generator[sqlite3.Connection, None, None]:
    """Open the registry database, creating it on first use.

    Commits on clean exit, rolls back if the body raises. The 5s timeout is
    SQLite's busy timeout — concurrent writers wait for each other instead
    of failing immediately.
    """
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    db_path = REGISTRY_DIR / "registry.db"
    is_new = not db_path.exists()
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(_SCHEMA)
        if is_new:
            db_path.chmod(0o600)  # entries hold server passwords
        yield conn
        conn.commit()
    finally:
        conn.close()


def _entry(row: sqlite3.Row) -> RegistryEntry:
    # Column names match RegistryEntry field names one-to-one.
    return RegistryEntry(**{name: row[name] for name in row.keys()})


def write(entry: RegistryEntry) -> None:
    """Write (insert or replace) a registry entry."""
    with _connect() as conn:
        conn.execute(_UPSERT, asdict(entry))


def read(key: str) -> RegistryEntry | None:
    """Read a registry entry by key. Returns None if not found."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM servers WHERE key = ?", (key,)).fetchone()
    return _entry(row) if row is not None else None


def delete(key: str) -> None:
    """Remove a registry entry. No-op if not found."""
    with _connect() as conn:
        conn.execute("DELETE FROM servers WHERE key = ?", (key,))


def list_all() -> list[RegistryEntry]:
    """Return all registry entries."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM servers").fetchall()
    return [_entry(row) for row in rows]


def claim_starting(entry: RegistryEntry) -> bool:
    """Atomically insert a 'starting' row for entry.key.

    Returns True if this call claimed the key, False if a row for the key
    already exists (a live 'starting' claim or a 'ready' server). A
    'starting' row older than _START_LEASE_SECONDS is treated as abandoned
    and reclaimed within the same transaction.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=_START_LEASE_SECONDS)).isoformat(
        timespec="microseconds"
    )
    try:
        with _connect() as conn:
            conn.execute(
                "DELETE FROM servers WHERE key = ? AND state = ? AND claimed_at < ?",
                (entry.key, ServerState.STARTING.value, cutoff),
            )
            conn.execute(_INSERT, asdict(entry))
        return True
    except sqlite3.IntegrityError:  # PRIMARY KEY conflict — someone else holds the key
        return False


def is_alive(pid: int | None) -> bool:
    """Return True if pid is set and a process with it is running."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def now_iso() -> str:
    """Return current UTC time as ISO-8601 string with fixed microsecond precision.

    Fixed precision (rather than omitting the fraction when it's exactly
    zero, as isoformat() does by default) keeps these strings correctly
    orderable by plain string comparison, e.g. in claim_starting()'s lease check.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")
