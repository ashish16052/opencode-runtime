"""Tests for the registry module."""

import os
import stat

import pytest

import opencode_harness.registry as registry
from opencode_harness.registry import RegistryEntry


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Redirect REGISTRY_DIR to a temp path for every test."""
    monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "servers")


def make_entry(**kwargs: object) -> RegistryEntry:
    defaults: dict[str, object] = dict(
        key="abc123def456abcd",
        pid=99999,
        port=54321,
        password="secret",
        project_dir="/tmp/project",
        server_dir=None,
        started_at="2026-07-05T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return RegistryEntry(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# write / read
# ---------------------------------------------------------------------------


def test_write_read_roundtrip():
    entry = make_entry()
    registry.write(entry)
    result = registry.read(entry.key)
    assert result == entry


def test_read_returns_none_for_missing_key():
    assert registry.read("doesnotexist") is None


def test_write_read_with_server_dir():
    entry = make_entry(server_dir="/tmp/runtime/servers/abc123")
    registry.write(entry)
    result = registry.read(entry.key)
    assert result is not None
    assert result.server_dir == "/tmp/runtime/servers/abc123"


# ---------------------------------------------------------------------------
# workspace / user_id
# ---------------------------------------------------------------------------


def test_write_read_with_workspace_and_user_id():
    entry = make_entry(workspace="org_a", user_id="u_1")
    registry.write(entry)
    result = registry.read(entry.key)
    assert result is not None
    assert result.workspace == "org_a"
    assert result.user_id == "u_1"


def test_read_old_entry_missing_workspace_defaults_to_none():
    """Old JSON files without workspace/user_id fields should load with None defaults."""
    import json

    registry.REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    old_data = dict(
        key="abc123def456abcd",
        pid=99999,
        port=54321,
        password="secret",
        project_dir="/tmp/project",
        server_dir=None,
        started_at="2026-07-05T00:00:00+00:00",
        # no workspace, no user_id
    )
    path = registry.REGISTRY_DIR / "abc123def456abcd.json"
    path.write_text(json.dumps(old_data), encoding="utf-8")
    result = registry.read("abc123def456abcd")
    assert result is not None
    assert result.workspace is None
    assert result.user_id is None


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_removes_file():
    entry = make_entry()
    registry.write(entry)
    registry.delete(entry.key)
    assert registry.read(entry.key) is None


def test_delete_is_noop_for_missing_key():
    registry.delete("doesnotexist")  # should not raise


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


def test_list_all_empty_when_no_registry_dir():
    # REGISTRY_DIR doesn't exist yet
    assert registry.list_all() == []


def test_list_all_returns_all_entries():
    entries = [make_entry(key=f"key{i:016x}") for i in range(3)]
    for e in entries:
        registry.write(e)
    result = registry.list_all()
    assert len(result) == 3
    assert {e.key for e in result} == {e.key for e in entries}


def test_list_all_skips_invalid_files(tmp_path):
    registry.REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    (registry.REGISTRY_DIR / "broken.json").write_text("not json", encoding="utf-8")
    registry.write(make_entry())
    result = registry.list_all()
    assert len(result) == 1


# ---------------------------------------------------------------------------
# file permissions
# ---------------------------------------------------------------------------


def test_write_sets_permissions_600():
    entry = make_entry()
    registry.write(entry)
    path = registry.REGISTRY_DIR / f"{entry.key}.json"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


# ---------------------------------------------------------------------------
# is_alive
# ---------------------------------------------------------------------------


def test_is_alive_current_process():
    assert registry.is_alive(os.getpid()) is True


def test_is_alive_dead_pid():
    assert registry.is_alive(99999999) is False
