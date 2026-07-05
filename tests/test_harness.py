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

    async def test_server_running_after_start(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        h = OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        )
        await h.start()
        entries = registry.list_all()
        assert len(entries) == 1
        assert registry.is_alive(entries[0].pid)
        await h.stop()

    async def test_client_reachable_after_start(self, harness):
        session = await harness.session()
        health = await session.raw_client.health()
        assert health["healthy"] is True

    async def test_server_gone_after_stop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        h = OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        )
        await h.start()
        entries = registry.list_all()
        assert len(entries) == 1
        pid = entries[0].pid
        await h.stop()
        assert not registry.is_alive(pid)
        assert registry.list_all() == []

    async def test_context_manager(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        async with OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ):
            assert len(registry.list_all()) == 1
        assert registry.list_all() == []


class TestEnvIsolation:
    async def test_server_uses_real_home(self, tmp_path):
        """No runtime_dir — server process inherits the real HOME."""
        async with OpenCodeHarness(project_dir=tmp_path) as h:
            session = await h.session()
            health = await session.raw_client.health()
            assert health["healthy"] is True
            assert h.runtime_dir is None

    async def test_server_uses_isolated_home(self, tmp_path):
        """runtime_dir is set — server dir created under runtime_dir/servers/."""
        async with OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ) as h:
            assert h.runtime_dir is not None
            session = await h.session()
            assert session.raw_client.base_url.startswith("http://127.0.0.1:")


class TestRegistryIntegration:
    async def test_registry_entry_written_on_start(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        async with OpenCodeHarness(project_dir=tmp_path) as h:
            session = await h.session()
            entries = registry.list_all()
            assert len(entries) == 1
            port = int(session.raw_client.base_url.split(":")[-1])
            assert entries[0].port == port

    async def test_registry_entry_deleted_on_stop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        async with OpenCodeHarness(project_dir=tmp_path):
            pass
        assert registry.list_all() == []

    async def test_registry_entry_stores_workspace_and_user_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        async with OpenCodeHarness(project_dir=tmp_path) as h:
            await h.session(workspace="org_a", user_id="u_1")
            entries = registry.list_all()
            # Two servers: default (from start()) + org_a/u_1
            ws_entry = next((e for e in entries if e.workspace == "org_a"), None)
            assert ws_entry is not None
            assert ws_entry.user_id == "u_1"

    async def test_library_attaches_to_existing_registry_server(self, tmp_path, monkeypatch):
        """If a registry entry exists and PID is alive, library attaches instead of spawning."""
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")

        h1 = OpenCodeHarness(project_dir=tmp_path)
        await h1.start()
        entries = registry.list_all()
        assert len(entries) == 1
        key = entries[0].key
        pid = entries[0].pid

        # Second harness with same config attaches to the same server
        h2 = OpenCodeHarness(project_dir=tmp_path)
        await h2.start()
        entries2 = registry.list_all()
        assert len(entries2) == 1  # still one server, not two
        assert entries2[0].pid == pid  # same process

        # h2 stop kills the shared server (no ownership distinction)
        await h2.stop()
        assert not registry.is_alive(pid)
        assert registry.read(key) is None

        # h1 stop is now a no-op
        await h1.stop()
        assert registry.list_all() == []
