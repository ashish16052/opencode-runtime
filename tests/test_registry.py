"""Tests for the registry module."""

import asyncio
import json
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import opencode_runtime.registry as registry
from opencode_runtime.exceptions import RegistryBusyError
from opencode_runtime.registry import RegistryEntry

pytestmark = pytest.mark.asyncio


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


def make_claim(**kwargs: object) -> RegistryEntry:
    """A claim entry, as claim_starting() expects — pid is unknown yet.

    started_at defaults to now (not make_entry()'s fixed placeholder date),
    since claim_starting()'s lease check compares it against the real clock.
    """
    defaults: dict[str, object] = dict(pid=None, started_at=registry.now_iso())
    defaults.update(kwargs)
    return make_entry(**defaults)


# ---------------------------------------------------------------------------
# write / read
# ---------------------------------------------------------------------------


async def test_write_read_roundtrip():
    entry = make_entry()
    registry.write(entry)
    result = registry.read(entry.key)
    assert result == entry


async def test_read_returns_none_for_missing_key():
    assert registry.read("doesnotexist") is None


async def test_write_read_with_server_dir():
    entry = make_entry(server_dir="/tmp/runtime/servers/abc123")
    registry.write(entry)
    result = registry.read(entry.key)
    assert result is not None
    assert result.server_dir == "/tmp/runtime/servers/abc123"


async def test_write_twice_replaces_entry():
    entry = make_entry()
    registry.write(entry)
    updated = make_entry(pid=11111, port=22222)
    registry.write(updated)
    result = registry.read(entry.key)
    assert result is not None
    assert result.pid == 11111
    assert result.port == 22222


async def test_write_with_null_pid():
    """A claim entry has no pid yet."""
    entry = make_entry(pid=None)
    registry.write(entry)
    result = registry.read(entry.key)
    assert result is not None
    assert result.pid is None


# ---------------------------------------------------------------------------
# workspace / user_id
# ---------------------------------------------------------------------------


async def test_write_read_with_workspace_and_user_id():
    entry = make_entry(workspace="org_a", user_id="u_1")
    registry.write(entry)
    result = registry.read(entry.key)
    assert result is not None
    assert result.workspace == "org_a"
    assert result.user_id == "u_1"


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_removes_entry():
    entry = make_entry()
    registry.write(entry)
    registry.delete(entry.key)
    assert registry.read(entry.key) is None


async def test_delete_is_noop_for_missing_key():
    registry.delete("doesnotexist")  # should not raise


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


async def test_list_all_empty_when_no_registry_dir():
    assert registry.list_all() == []


async def test_list_all_returns_all_entries():
    entries = [make_entry(key=f"key{i:016x}") for i in range(3)]
    for e in entries:
        registry.write(e)
    result = registry.list_all()
    assert len(result) == 3
    assert {e.key for e in result} == {e.key for e in entries}


# ---------------------------------------------------------------------------
# file permissions
# ---------------------------------------------------------------------------


async def test_entry_file_created_with_permissions_600():
    entry = make_entry()
    registry.write(entry)
    path = registry.REGISTRY_DIR / f"{entry.key}.json"
    assert path.exists()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


# ---------------------------------------------------------------------------
# claim_starting
# ---------------------------------------------------------------------------


async def test_claim_starting_succeeds_for_new_key():
    entry = make_claim()
    assert registry.claim_starting(entry) is True
    result = registry.read(entry.key)
    assert result is not None
    assert result.pid is None


async def test_claim_starting_fails_for_live_claim():
    entry = make_claim()
    assert registry.claim_starting(entry) is True
    other = make_claim(port=55555)
    assert registry.claim_starting(other) is False
    # Original claim is untouched by the failed attempt.
    result = registry.read(entry.key)
    assert result is not None
    assert result.port == entry.port


async def test_claim_starting_after_delete_succeeds():
    entry = make_claim()
    assert registry.claim_starting(entry) is True
    registry.delete(entry.key)
    assert registry.claim_starting(entry) is True


async def test_concurrent_claim_starting_only_one_winner():
    claims = [make_claim(), make_claim(port=55555), make_claim(port=66666)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(registry.claim_starting, claims))
    assert results.count(True) == 1
    assert results.count(False) == 2


async def test_claim_starting_reclaims_after_lease_expires(monkeypatch):
    monkeypatch.setattr(registry, "_START_LEASE_SECONDS", 0)
    entry = make_claim()
    assert registry.claim_starting(entry) is True
    await asyncio.sleep(0.01)
    # No write()-to-ready call — simulates a crashed starter. A fresh claim
    # attempt should reclaim it once the (now zero-second) lease has expired.
    assert registry.claim_starting(make_claim(port=55555)) is True


async def test_claim_starting_does_not_reclaim_before_lease_expires():
    entry = make_claim()
    assert registry.claim_starting(entry) is True
    assert registry.claim_starting(make_claim(port=55555)) is False


async def test_claim_starting_does_not_reclaim_entry_with_pid():
    """An entry that already has a pid isn't touched by the lease logic,
    no matter how old its started_at is."""
    entry = make_entry()  # has a pid
    registry.write(entry)
    assert registry.claim_starting(make_claim(port=55555)) is False
    result = registry.read(entry.key)
    assert result is not None
    assert result.pid == entry.pid
    assert result.port == entry.port


# ---------------------------------------------------------------------------
# delete_if_instance
# ---------------------------------------------------------------------------


async def test_delete_if_instance_deletes_on_match():
    entry = make_entry(instance_id="gen-1")
    registry.write(entry)
    assert registry.delete_if_instance(entry.key, "gen-1") is True
    assert registry.read(entry.key) is None


async def test_delete_if_instance_leaves_mismatched_generation():
    entry = make_entry(instance_id="gen-2")
    registry.write(entry)
    assert registry.delete_if_instance(entry.key, "gen-1") is False
    assert registry.read(entry.key) is not None


async def test_delete_if_instance_false_for_missing_key():
    assert registry.delete_if_instance("doesnotexist", "gen-1") is False


async def test_delete_if_instance_reclaims_stale_lock():
    """A lock file left behind by a crashed holder doesn't wedge the registry."""
    entry = make_entry(instance_id="gen-1")
    registry.write(entry)
    lock_path = registry.REGISTRY_DIR / f"{entry.key}.lock"
    lock_path.write_text("", encoding="utf-8")
    stale = time.time() - registry._LOCK_STALE_SECONDS - 1
    os.utime(lock_path, (stale, stale))

    assert registry.delete_if_instance(entry.key, "gen-1") is True
    assert registry.read(entry.key) is None


async def test_delete_if_instance_raises_when_genuinely_locked():
    entry = make_entry(instance_id="gen-1")
    registry.write(entry)
    lock_path = registry.REGISTRY_DIR / f"{entry.key}.lock"
    lock_path.write_text("", encoding="utf-8")

    with pytest.raises(RegistryBusyError):
        registry.delete_if_instance(entry.key, "gen-1")


# ---------------------------------------------------------------------------
# forward/backward compatibility
# ---------------------------------------------------------------------------


async def test_read_tolerates_unknown_fields():
    """A file written by a newer version can carry fields this version
    doesn't know about — they should be ignored, not raise."""
    entry = make_entry()
    registry.write(entry)
    path = registry.REGISTRY_DIR / f"{entry.key}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["from_a_future_version"] = "whatever"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = registry.read(entry.key)
    assert result is not None
    assert result.key == entry.key


async def test_read_defaults_fields_missing_from_older_writer():
    """A file written before instance_id/pid_start_time existed should load
    fine, with those fields defaulting to None."""
    entry = make_entry()
    registry.write(entry)
    path = registry.REGISTRY_DIR / f"{entry.key}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["instance_id"]
    del data["pid_start_time"]
    path.write_text(json.dumps(data), encoding="utf-8")

    result = registry.read(entry.key)
    assert result is not None
    assert result.instance_id is None
    assert result.pid_start_time is None
