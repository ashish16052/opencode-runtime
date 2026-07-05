"""
Tests for internal server helpers — no opencode binary required.
Tests for ServerManager — require the real opencode binary.
"""

import os
import signal
from pathlib import Path

import opencode_harness.registry as registry
from opencode_harness.server import (
    ServerManager,
    _ManagedServer,
    _compute_runtime_key,
)


class TestComputeRuntimeKey:
    def test_same_inputs_produce_same_key(self, tmp_path):
        k1 = _compute_runtime_key("acme", "u_1", tmp_path, None, {})
        k2 = _compute_runtime_key("acme", "u_1", tmp_path, None, {})
        assert k1 == k2

    def test_different_workspace_different_key(self, tmp_path):
        k1 = _compute_runtime_key("acme", "u_1", tmp_path, None, {})
        k2 = _compute_runtime_key("beta", "u_1", tmp_path, None, {})
        assert k1 != k2

    def test_different_user_different_key(self, tmp_path):
        k1 = _compute_runtime_key("acme", "u_1", tmp_path, None, {})
        k2 = _compute_runtime_key("acme", "u_2", tmp_path, None, {})
        assert k1 != k2

    def test_different_config_different_key(self, tmp_path):
        k1 = _compute_runtime_key(None, None, tmp_path, None, {"model": "a"})
        k2 = _compute_runtime_key(None, None, tmp_path, None, {"model": "b"})
        assert k1 != k2

    def test_different_materials_different_key(self, tmp_path):
        k1 = _compute_runtime_key(None, None, tmp_path, "./mat/a", {})
        k2 = _compute_runtime_key(None, None, tmp_path, "./mat/b", {})
        assert k1 != k2

    def test_none_workspace_and_user_stable(self, tmp_path):
        """No workspace/user → still produces a stable key (default server)."""
        k1 = _compute_runtime_key(None, None, tmp_path, None, {})
        k2 = _compute_runtime_key(None, None, tmp_path, None, {})
        assert k1 == k2

    def test_key_is_16_chars(self, tmp_path):
        key = _compute_runtime_key("acme", "u_1", tmp_path, None, {})
        assert len(key) == 16

    def test_key_is_hex(self, tmp_path):
        key = _compute_runtime_key("acme", "u_1", tmp_path, None, {})
        int(key, 16)  # raises if not valid hex


class TestManagedServer:
    def test_fields_accessible(self):
        """_ManagedServer stores key, process, client, server_dir."""
        server = _ManagedServer(
            key="abc123",
            process=None,  # type: ignore[arg-type]
            client=None,  # type: ignore[arg-type]
            server_dir=Path("/tmp/test"),
        )
        assert server.key == "abc123"
        assert server.server_dir == Path("/tmp/test")

    def test_server_dir_can_be_none(self):
        """server_dir is None when no runtime_dir (no isolation)."""
        server = _ManagedServer(
            key="abc123",
            process=None,  # type: ignore[arg-type]
            client=None,  # type: ignore[arg-type]
            server_dir=None,
        )
        assert server.server_dir is None


class TestServerManager:
    async def test_get_or_start_starts_server(self, tmp_path):
        manager = ServerManager()
        key = _compute_runtime_key(None, None, tmp_path, None, {})
        server = await manager.get_or_start(
            key=key,
            project_dir=tmp_path,
            server_dir=tmp_path / "srv",
            materials=None,
            config={},
            env={},
        )
        entry = registry.read(key)
        assert entry is not None
        assert registry.is_alive(entry.pid)
        assert server.client is not None
        await manager.stop_all()

    async def test_same_key_reuses_server(self, tmp_path):
        """Same key → same server (same port in registry)."""
        manager = ServerManager()
        key = _compute_runtime_key(None, None, tmp_path, None, {})
        s1 = await manager.get_or_start(
            key=key,
            project_dir=tmp_path,
            server_dir=tmp_path / "srv",
            materials=None,
            config={},
            env={},
        )
        s2 = await manager.get_or_start(
            key=key,
            project_dir=tmp_path,
            server_dir=tmp_path / "srv",
            materials=None,
            config={},
            env={},
        )
        assert s1.client.base_url == s2.client.base_url
        await manager.stop_all()

    async def test_different_key_starts_different_server(self, tmp_path):
        manager = ServerManager()
        k1 = _compute_runtime_key("acme", None, tmp_path, None, {})
        k2 = _compute_runtime_key("beta", None, tmp_path, None, {})
        s1 = await manager.get_or_start(
            key=k1,
            project_dir=tmp_path,
            server_dir=tmp_path / "acme",
            materials=None,
            config={},
            env={},
        )
        s2 = await manager.get_or_start(
            key=k2,
            project_dir=tmp_path,
            server_dir=tmp_path / "beta",
            materials=None,
            config={},
            env={},
        )
        assert s1.client.base_url != s2.client.base_url
        await manager.stop_all()

    async def test_stop_all_terminates_all(self, tmp_path):
        manager = ServerManager()
        k1 = _compute_runtime_key("acme", None, tmp_path, None, {})
        k2 = _compute_runtime_key("beta", None, tmp_path, None, {})
        e1_before = None
        e2_before = None
        await manager.get_or_start(
            key=k1,
            project_dir=tmp_path,
            server_dir=tmp_path / "acme",
            materials=None,
            config={},
            env={},
        )
        await manager.get_or_start(
            key=k2,
            project_dir=tmp_path,
            server_dir=tmp_path / "beta",
            materials=None,
            config={},
            env={},
        )
        e1_before = registry.read(k1)
        e2_before = registry.read(k2)
        assert e1_before is not None
        assert e2_before is not None

        await manager.stop_all()

        assert not registry.is_alive(e1_before.pid)
        assert not registry.is_alive(e2_before.pid)
        assert registry.read(k1) is None
        assert registry.read(k2) is None

    async def test_server_dir_created(self, tmp_path):
        manager = ServerManager()
        key = _compute_runtime_key(None, None, tmp_path, None, {})
        server_dir = tmp_path / "srv"
        await manager.get_or_start(
            key=key,
            project_dir=tmp_path,
            server_dir=server_dir,
            materials=None,
            config={},
            env={},
        )
        assert server_dir.exists()
        assert (server_dir / "tmp").exists()
        assert (server_dir / "opencode.log").exists()
        await manager.stop_all()

    async def test_stop_single_server(self, tmp_path):
        """stop(key) terminates one server, leaves others running."""
        manager = ServerManager()
        k1 = _compute_runtime_key("acme", None, tmp_path, None, {})
        k2 = _compute_runtime_key("beta", None, tmp_path, None, {})
        await manager.get_or_start(
            key=k1,
            project_dir=tmp_path,
            server_dir=tmp_path / "acme",
            materials=None,
            config={},
            env={},
        )
        await manager.get_or_start(
            key=k2,
            project_dir=tmp_path,
            server_dir=tmp_path / "beta",
            materials=None,
            config={},
            env={},
        )
        e1 = registry.read(k1)
        e2 = registry.read(k2)
        assert e1 is not None
        assert e2 is not None

        await manager.stop(k1)
        assert registry.read(k1) is None
        assert not registry.is_alive(e1.pid)
        assert registry.read(k2) is not None  # still running
        assert registry.is_alive(e2.pid)
        await manager.stop_all()

    async def test_stop_nonexistent_key_is_noop(self, tmp_path):
        """stop(key) on an unknown key does not raise."""
        manager = ServerManager()
        await manager.stop("nonexistent")  # should not raise


class TestServerManagerRegistry:
    async def test_start_writes_registry_entry(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        manager = ServerManager()
        key = _compute_runtime_key(None, None, tmp_path, None, {})
        server = await manager.get_or_start(
            key=key,
            project_dir=tmp_path,
            server_dir=tmp_path / "srv",
            materials=None,
            config={},
            env={},
        )
        entry = registry.read(key)
        assert entry is not None
        assert registry.is_alive(entry.pid)
        assert entry.port == int(server.client.base_url.split(":")[-1])
        await manager.stop_all()

    async def test_stop_deletes_registry_entry(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        manager = ServerManager()
        key = _compute_runtime_key(None, None, tmp_path, None, {})
        await manager.get_or_start(
            key=key,
            project_dir=tmp_path,
            server_dir=tmp_path / "srv",
            materials=None,
            config={},
            env={},
        )
        await manager.stop(key)
        assert registry.read(key) is None

    async def test_attaches_to_alive_registry_entry(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        manager1 = ServerManager()
        key = _compute_runtime_key(None, None, tmp_path, None, {})
        s1 = await manager1.get_or_start(
            key=key,
            project_dir=tmp_path,
            server_dir=tmp_path / "srv",
            materials=None,
            config={},
            env={},
        )
        e1 = registry.read(key)
        assert e1 is not None

        # Second manager sees the registry entry and attaches to the same port
        manager2 = ServerManager()
        s2 = await manager2.get_or_start(
            key=key,
            project_dir=tmp_path,
            server_dir=tmp_path / "srv",
            materials=None,
            config={},
            env={},
        )
        assert s2.client.base_url == s1.client.base_url  # same server

        # manager2 stop kills the process and deletes registry
        await manager2.stop(key)
        assert registry.read(key) is None
        assert not registry.is_alive(e1.pid)

        # manager1 stop is now a no-op (already gone)
        await manager1.stop_all()

    async def test_stale_registry_entry_cleaned_on_attach(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        # Write a stale entry with a dead PID
        from opencode_harness.registry import RegistryEntry, now_iso

        key = _compute_runtime_key(None, None, tmp_path, None, {})
        registry.write(
            RegistryEntry(
                key=key,
                pid=99999999,
                port=54321,
                password="stale",
                project_dir=str(tmp_path),
                server_dir=None,
                started_at=now_iso(),
            )
        )

        manager = ServerManager()
        server = await manager.get_or_start(
            key=key,
            project_dir=tmp_path,
            server_dir=tmp_path / "srv",
            materials=None,
            config={},
            env={},
        )
        # Fresh server was spawned — registry has a new alive entry
        entry = registry.read(key)
        assert entry is not None
        assert registry.is_alive(entry.pid)
        assert entry.pid != 99999999
        assert entry.port == int(server.client.base_url.split(":")[-1])
        await manager.stop_all()

    async def test_start_stores_workspace_and_user_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        manager = ServerManager()
        key = _compute_runtime_key("org_a", "u_1", tmp_path, None, {})
        await manager.get_or_start(
            key=key,
            project_dir=tmp_path,
            server_dir=tmp_path / "srv",
            materials=None,
            config={},
            env={},
            workspace="org_a",
            user_id="u_1",
        )
        entry = registry.read(key)
        assert entry is not None
        assert entry.workspace == "org_a"
        assert entry.user_id == "u_1"
        await manager.stop_all()

    async def test_server_restarted_after_external_kill(self, tmp_path, monkeypatch):
        """If registry entry is deleted externally, next call starts a fresh server."""
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        manager = ServerManager()
        key = _compute_runtime_key(None, None, tmp_path, None, {})
        s1 = await manager.get_or_start(
            key=key,
            project_dir=tmp_path,
            server_dir=tmp_path / "srv",
            materials=None,
            config={},
            env={},
        )
        old_entry = registry.read(key)
        assert old_entry is not None
        old_pid = old_entry.pid

        # Simulate CLI stop-all: kill process + delete registry entry
        os.kill(old_pid, signal.SIGTERM)
        await s1.process.wait()
        registry.delete(key)

        # Next call should start a fresh server
        await manager.get_or_start(
            key=key,
            project_dir=tmp_path,
            server_dir=tmp_path / "srv",
            materials=None,
            config={},
            env={},
        )
        new_entry = registry.read(key)
        assert new_entry is not None
        assert new_entry.pid != old_pid
        assert registry.is_alive(new_entry.pid)
        await manager.stop_all()
