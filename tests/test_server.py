"""
Tests for internal server helpers — no opencode binary required.
Tests for ServerManager — require the real opencode binary.
"""

from pathlib import Path

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
        assert server.process.returncode is None  # still running
        assert server.client is not None
        await manager.stop_all()

    async def test_same_key_reuses_server(self, tmp_path):
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
        assert s1 is s2
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
        assert s1 is not s2
        assert s1.client.base_url != s2.client.base_url
        await manager.stop_all()

    async def test_stop_all_terminates_all(self, tmp_path):
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
        p1, p2 = s1.process, s2.process
        await manager.stop_all()
        assert len(manager._servers) == 0
        assert p1.returncode is not None
        assert p2.returncode is not None

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
        await manager.stop(k1)
        assert k1 not in manager._servers
        assert s1.process.returncode is not None  # terminated
        assert k2 in manager._servers
        assert s2.process.returncode is None  # still running
        await manager.stop_all()

    async def test_stop_nonexistent_key_is_noop(self, tmp_path):
        """stop(key) on an unknown key does not raise."""
        manager = ServerManager()
        await manager.stop("nonexistent")  # should not raise
