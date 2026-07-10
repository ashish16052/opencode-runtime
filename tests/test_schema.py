"""Tests for the registry's schema versioning and migration mechanism."""

import sqlite3

import pytest

import opencode_runtime.registry as registry
from opencode_runtime import schema
from opencode_runtime.exceptions import RegistrySchemaError
from opencode_runtime.registry import RegistryEntry, ServerState


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Redirect REGISTRY_DIR to a temp path for every test."""
    monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "servers")


def make_entry(**kwargs: object) -> RegistryEntry:
    defaults: dict[str, object] = dict(
        key="abc123def456abcd",
        state=ServerState.RUNNING,
        pid=99999,
        port=54321,
        password="secret",
        project_dir="/tmp/project",
        server_dir=None,
        started_at="2026-07-05T00:00:00+00:00",
        claimed_at="2026-07-05T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return RegistryEntry(**defaults)  # type: ignore[arg-type]


def _db_path(tmp_path):
    return tmp_path / "servers" / "registry.db"


def _user_version(db_path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def test_fresh_database_is_stamped_with_current_version(tmp_path):
    registry.write(make_entry())
    assert _user_version(_db_path(tmp_path)) == schema.SCHEMA_VERSION


def test_legacy_unversioned_database_is_migrated_on_open(tmp_path):
    """A database created before versioning existed has user_version 0 by
    default. Opening it through the registry should bring it up to the
    current version without losing data or erroring."""
    db_path = _db_path(tmp_path)
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(schema.SCHEMA)
        conn.execute(
            "INSERT INTO servers (key, state, pid, port, password, project_dir, "
            "server_dir, started_at, claimed_at) VALUES "
            "('legacykey00000000', 'running', 123, 4096, 'pw', '/tmp/p', NULL, "
            "'2026-07-05T00:00:00+00:00', '2026-07-05T00:00:00+00:00')"
        )
        conn.commit()
    finally:
        conn.close()
    assert _user_version(db_path) == 0

    entry = registry.read("legacykey00000000")
    assert entry is not None
    assert entry.pid == 123
    assert _user_version(db_path) == schema.SCHEMA_VERSION


def test_future_schema_version_raises(tmp_path):
    """A database written by a newer opencode-runtime must not be silently
    treated as compatible — it should fail loudly instead of risking data
    loss or a confusing runtime error deeper in the stack."""
    db_path = _db_path(tmp_path)
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(schema.SCHEMA)
        conn.execute(f"PRAGMA user_version = {schema.SCHEMA_VERSION + 1}")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RegistrySchemaError):
        registry.read("doesnotexist")


def test_migrate_raises_on_missing_step(monkeypatch):
    """If SCHEMA_VERSION is bumped without registering the matching migration
    step, migrate() must fail loudly rather than silently skip a step."""
    monkeypatch.setattr(schema, "SCHEMA_VERSION", 2)
    with pytest.raises(RegistrySchemaError):
        schema.migrate(sqlite3.connect(":memory:"), current_version=0)


def test_migrate_raises_on_newer_version():
    with pytest.raises(RegistrySchemaError):
        schema.migrate(sqlite3.connect(":memory:"), current_version=schema.SCHEMA_VERSION + 1)
