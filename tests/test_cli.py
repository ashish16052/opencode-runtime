"""CLI integration tests.

Tests for ps, stop, stop-all, health use pre-populated registry entries and
call cmd_* functions directly — no real servers needed.

Tests for serve use the real opencode binary.
"""

from __future__ import annotations

import argparse
import os

import pytest

import opencode_harness.registry as registry
from opencode_harness.cli import cmd_health, cmd_ps, cmd_serve, cmd_stop, cmd_stop_all
from opencode_harness.registry import RegistryEntry


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Redirect REGISTRY_DIR to a temp path for every test."""
    monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "servers")


def ns(**kwargs: object) -> argparse.Namespace:
    """Build a minimal Namespace for CLI commands."""
    return argparse.Namespace(**kwargs)


def make_entry(**kwargs: object) -> RegistryEntry:
    defaults: dict[str, object] = dict(
        key="abc123def456abcd",
        pid=os.getpid(),  # alive by default
        port=54321,
        password="secret",
        project_dir="/tmp/project",
        server_dir=None,
        started_at="2026-07-05T00:00:00+00:00",
        workspace=None,
        user_id=None,
    )
    defaults.update(kwargs)
    return RegistryEntry(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ps
# ---------------------------------------------------------------------------


def test_ps_empty_shows_header(capsys):
    cmd_ps(ns())
    out = capsys.readouterr().out
    assert "ID" in out
    assert "STATUS" in out


def test_ps_shows_alive_entry(capsys):
    registry.write(make_entry())
    cmd_ps(ns())
    out = capsys.readouterr().out
    assert "abc123def456abcd" in out
    assert "alive" in out


def test_ps_shows_dead_entry(capsys):
    registry.write(make_entry(pid=99999999))
    cmd_ps(ns())
    out = capsys.readouterr().out
    assert "dead" in out


def test_ps_shows_workspace_user_columns_when_set(capsys):
    registry.write(make_entry(workspace="org_a", user_id="u_1"))
    cmd_ps(ns())
    out = capsys.readouterr().out
    assert "WORKSPACE" in out
    assert "USER" in out
    assert "org_a" in out
    assert "u_1" in out


def test_ps_hides_workspace_user_columns_when_not_set(capsys):
    registry.write(make_entry())
    cmd_ps(ns())
    out = capsys.readouterr().out
    assert "WORKSPACE" not in out
    assert "USER" not in out


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_unknown_key_exits():
    with pytest.raises(SystemExit):
        cmd_stop(ns(key="doesnotexist"))


def test_stop_dead_process_warns_and_deletes(capsys):
    registry.write(make_entry(pid=99999999))
    cmd_stop(ns(key="abc123def456abcd"))
    out = capsys.readouterr().out
    assert "already dead" in out
    assert registry.read("abc123def456abcd") is None


def test_stop_live_server(tmp_path):
    """Start a real server, stop it via cmd_stop, verify PID is dead."""
    args = ns(
        project_dir=str(tmp_path),
        runtime_dir=None,
        materials=None,
        workspace=None,
        user_id=None,
    )
    cmd_serve(args)

    entries = registry.list_all()
    assert len(entries) == 1
    entry = entries[0]
    assert registry.is_alive(entry.pid)

    cmd_stop(ns(key=entry.key))

    assert not registry.is_alive(entry.pid)
    assert registry.read(entry.key) is None


# ---------------------------------------------------------------------------
# stop-all
# ---------------------------------------------------------------------------


def test_stop_all_no_servers(capsys):
    cmd_stop_all(ns())
    out = capsys.readouterr().out
    assert "no servers" in out


def test_stop_all_kills_all(tmp_path):
    """Start two real servers, stop-all kills both."""
    for subdir in ("proj_a", "proj_b"):
        d = tmp_path / subdir
        d.mkdir()
        args = ns(
            project_dir=str(d),
            runtime_dir=None,
            materials=None,
            workspace=subdir,
            user_id=None,
        )
        cmd_serve(args)

    entries = registry.list_all()
    assert len(entries) == 2
    pids = [e.pid for e in entries]

    cmd_stop_all(ns())

    for pid in pids:
        assert not registry.is_alive(pid)
    assert registry.list_all() == []


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def test_health_unknown_key_exits():
    with pytest.raises(SystemExit):
        cmd_health(ns(key="doesnotexist"))


def test_health_live_server(tmp_path, capsys):
    """Start a real server, check health, stop it."""
    args = ns(
        project_dir=str(tmp_path),
        runtime_dir=None,
        materials=None,
        workspace=None,
        user_id=None,
    )
    cmd_serve(args)

    entries = registry.list_all()
    assert len(entries) == 1
    key = entries[0].key

    cmd_health(ns(key=key))
    out = capsys.readouterr().out
    assert "healthy" in out

    cmd_stop(ns(key=key))


def test_health_dead_server(capsys):
    """Registry entry exists but process is dead — health should fail."""
    registry.write(make_entry(pid=99999999, port=19999))
    with pytest.raises(SystemExit):
        cmd_health(ns(key="abc123def456abcd"))


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


def test_serve_starts_server(tmp_path):
    args = ns(
        project_dir=str(tmp_path),
        runtime_dir=None,
        materials=None,
        workspace=None,
        user_id=None,
    )
    cmd_serve(args)

    entries = registry.list_all()
    assert len(entries) == 1
    assert registry.is_alive(entries[0].pid)

    # cleanup
    cmd_stop(ns(key=entries[0].key))


def test_serve_duplicate_key_exits(tmp_path):
    args = ns(
        project_dir=str(tmp_path),
        runtime_dir=None,
        materials=None,
        workspace=None,
        user_id=None,
    )
    cmd_serve(args)

    with pytest.raises(SystemExit):
        cmd_serve(args)

    # cleanup
    entries = registry.list_all()
    for e in entries:
        cmd_stop(ns(key=e.key))


def test_serve_stale_entry_cleaned_and_restarted(tmp_path):
    """Dead PID in registry — serve should clean it up and start fresh."""
    from opencode_harness.server import _compute_runtime_key
    from pathlib import Path

    # Compute the same key serve will use
    key = _compute_runtime_key(
        workspace=None, user_id=None, project_dir=Path(tmp_path), materials=None, config={}
    )
    registry.write(make_entry(key=key, pid=99999999, project_dir=str(tmp_path)))

    args = ns(
        project_dir=str(tmp_path),
        runtime_dir=None,
        materials=None,
        workspace=None,
        user_id=None,
    )
    cmd_serve(args)

    entries = registry.list_all()
    assert len(entries) == 1
    assert registry.is_alive(entries[0].pid)

    # cleanup
    cmd_stop(ns(key=entries[0].key))


def test_serve_with_workspace_and_user_id(tmp_path):
    args = ns(
        project_dir=str(tmp_path),
        runtime_dir=None,
        materials=None,
        workspace="org_a",
        user_id="u_1",
    )
    cmd_serve(args)

    entries = registry.list_all()
    assert len(entries) == 1
    assert entries[0].workspace == "org_a"
    assert entries[0].user_id == "u_1"

    cmd_stop(ns(key=entries[0].key))
