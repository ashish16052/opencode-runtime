"""
Tests for OpenCodeHarness lifecycle against the real opencode binary.

Covers: start / stop, process management, context manager, runtime dir,
        registry integration (library-started servers appear in registry).
No model or agent calls — no API keys required.
"""

import pytest

import opencode_harness.registry as registry
from opencode_harness import OpenCodeHarness

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def harness(tmp_path):
    """Start a harness against tmp_path and stop it after the test."""
    h = OpenCodeHarness(
        project_dir=tmp_path,
        runtime_dir=tmp_path / "runtime",
    )
    await h.start()
    yield h
    await h.stop()


def _servers(h: OpenCodeHarness) -> list:
    return list(h._server_manager._servers.values())


class TestHarnessLifecycle:
    async def test_start_creates_runtime_dir(self, tmp_path):
        h = OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        )
        await h.start()
        assert h.runtime_dir is not None
        assert h.runtime_dir.exists()
        await h.stop()

    async def test_process_running_after_start(self, harness):
        servers = _servers(harness)
        assert len(servers) == 1
        assert servers[0].process is not None
        assert servers[0].process.returncode is None  # still running

    async def test_client_set_after_start(self, harness):
        servers = _servers(harness)
        assert len(servers) == 1
        assert servers[0].client.base_url.startswith("http://127.0.0.1:")

    async def test_process_gone_after_stop(self, tmp_path):
        h = OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        )
        await h.start()
        servers = _servers(h)
        assert len(servers) == 1
        process = servers[0].process
        assert process is not None
        await h.stop()
        assert len(h._server_manager._servers) == 0
        assert process.returncode is not None

    async def test_context_manager(self, tmp_path):
        async with OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ) as h:
            assert len(_servers(h)) == 1
        assert len(h._server_manager._servers) == 0


class TestEnvIsolation:
    async def test_server_uses_real_home(self, tmp_path):
        """No runtime_dir — server process inherits the real HOME."""
        async with OpenCodeHarness(project_dir=tmp_path) as h:
            servers = _servers(h)
            assert len(servers) == 1
            health = await servers[0].client.health()
            assert health["healthy"] is True
            assert h.runtime_dir is None

    async def test_server_uses_isolated_home(self, tmp_path):
        """runtime_dir is set — server dir created under runtime_dir/servers/."""
        async with OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ) as h:
            assert h.runtime_dir is not None
            servers = _servers(h)
            assert len(servers) == 1
            assert servers[0].server_dir is not None
            assert (servers[0].server_dir / "tmp").exists()


class TestRegistryIntegration:
    async def test_registry_entry_written_on_start(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        async with OpenCodeHarness(project_dir=tmp_path) as h:
            servers = _servers(h)
            key = servers[0].key
            entry = registry.read(key)
            assert entry is not None
            assert entry.pid == servers[0].process.pid
            assert entry.key == key

    async def test_registry_entry_deleted_on_stop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        async with OpenCodeHarness(project_dir=tmp_path) as h:
            key = _servers(h)[0].key
        # After __aexit__ the owned server should be cleaned from registry
        assert registry.read(key) is None

    async def test_registry_entry_stores_workspace_and_user_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        async with OpenCodeHarness(project_dir=tmp_path) as h:
            session = await h.session(workspace="org_a", user_id="u_1")  # noqa: F841
            entries = registry.list_all()
            # Two servers: default (from start()) + org_a/u_1
            ws_entry = next((e for e in entries if e.workspace == "org_a"), None)
            assert ws_entry is not None
            assert ws_entry.user_id == "u_1"

    async def test_library_attaches_to_existing_registry_server(self, tmp_path, monkeypatch):
        """If a registry entry exists and PID is alive, library attaches instead of spawning."""
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")

        # First harness starts and owns the server
        h1 = OpenCodeHarness(project_dir=tmp_path)
        await h1.start()
        key = _servers(h1)[0].key
        pid = _servers(h1)[0].process.pid

        # Second harness with same config should attach, not spawn
        h2 = OpenCodeHarness(project_dir=tmp_path)
        await h2.start()
        servers2 = _servers(h2)
        assert len(servers2) == 1
        assert servers2[0].process is None  # attached — not owned
        assert servers2[0].key == key

        # h2 stop detaches only — server still alive
        await h2.stop()
        assert registry.is_alive(pid)  # process still running
        assert registry.read(key) is not None  # registry entry intact

        # h1 stop kills and cleans up
        await h1.stop()
        assert not registry.is_alive(pid)
        assert registry.read(key) is None
