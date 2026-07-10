"""
SQLite schema and migration mechanism for the registry database.

The schema version is stored in the database itself via SQLite's built-in
`PRAGMA user_version` — no separate metadata table needed. A fresh database
is stamped with SCHEMA_VERSION directly; an existing one is brought up to it
by migrate().

Bump SCHEMA_VERSION and add a step to _MIGRATIONS whenever the `servers`
table shape changes. Never edit SCHEMA in place once a version has shipped —
existing databases on disk still have the old shape and rely on a migration
step to reach the new one.
"""

from __future__ import annotations

import sqlite3
from typing import Callable

from .exceptions import RegistrySchemaError

SCHEMA = """
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

SCHEMA_VERSION = 1

# Migration steps, keyed by the version they upgrade *from*. Each callable
# receives the open connection (mid-transaction, table already created) and
# must leave the database in the shape of `from_version + 1` — e.g. an
# `ALTER TABLE servers ADD COLUMN ...`.
#
# Version 0 covers every database that predates this versioning scheme. Its
# table shape is identical to version 1 (versioning was introduced with no
# accompanying column change), so that migration is a no-op — it exists so
# unversioned databases get stamped with a version the first time they're
# opened under this scheme.
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    0: lambda conn: None,
}


def migrate(conn: sqlite3.Connection, current_version: int) -> None:
    """Bring conn's schema from current_version up to SCHEMA_VERSION, in place.

    Raises RegistrySchemaError if current_version is newer than this code
    understands, or if a required migration step isn't registered.
    """
    if current_version > SCHEMA_VERSION:
        raise RegistrySchemaError(
            f"registry database is schema v{current_version}, but this version of "
            f"opencode-runtime only understands up to v{SCHEMA_VERSION}. Upgrade "
            "opencode-runtime to use it."
        )
    version = current_version
    while version < SCHEMA_VERSION:
        step = _MIGRATIONS.get(version)
        if step is None:
            raise RegistrySchemaError(
                f"no migration registered to bring the registry database from schema "
                f"v{version} to v{version + 1}"
            )
        step(conn)
        version += 1
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
