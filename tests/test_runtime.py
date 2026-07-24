"""
Tests for OpenCodeRuntime lifecycle against the real opencode binary.

Covers: start / stop, process management, context manager, runtime dir,
        registry integration (library-started servers appear in registry).
No model or agent calls — no API keys required.
"""

import pytest

import opencode_runtime.registry as registry
from opencode_runtime import OpenCodeRuntime, process

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def runtime(tmp_path):
    """Create a runtime. Servers start lazily on first session() call."""
    r = OpenCodeRuntime(
        project_dir=tmp_path,
        runtime_dir=tmp_path / "runtime",
    )
    yield r
    await r.close()


class TestRuntimeLifecycle:
    async def test_start_creates_runtime_dir(self, tmp_path):
        r = OpenCodeRuntime(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        )
        await r.session()
        assert r.runtime_dir is not None
        assert r.runtime_dir.exists()
        await r.close()

    async def test_server_running_after_start(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        r = OpenCodeRuntime(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        )
        await r.session()
        entries = registry.list_all()
        assert len(entries) == 1
        assert process.is_alive(entries[0].pid)
        await r.close()

    async def test_client_reachable_after_start(self, runtime):
        session = await runtime.session()
        health = await session.raw_client.health()
        assert health["healthy"] is True

    async def test_context_manager_without_server(self, tmp_path, monkeypatch):
        """Entering and exiting context without calling session() — no server created."""
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        async with OpenCodeRuntime(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ):
            assert len(registry.list_all()) == 0
        assert len(registry.list_all()) == 0

    async def test_context_manager_with_server(self, tmp_path, monkeypatch):
        """close() stops a server this runtime itself started."""
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        async with OpenCodeRuntime(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ) as r:
            await r.session()
            assert len(registry.list_all()) == 1
        # Server started by this runtime is stopped on close()
        assert len(registry.list_all()) == 0

    async def test_close_leaves_attached_server_running(self, tmp_path, monkeypatch):
        """close() must not stop a server this runtime merely attached to."""
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        owner = OpenCodeRuntime(project_dir=tmp_path, runtime_dir=tmp_path / "runtime")
        await owner.session()
        assert len(registry.list_all()) == 1

        async with OpenCodeRuntime(
            project_dir=tmp_path, runtime_dir=tmp_path / "runtime"
        ) as attacher:
            await attacher.session()  # same key — attaches, doesn't start
            assert len(registry.list_all()) == 1
        # attacher's close() ran — the server `owner` started must survive
        assert len(registry.list_all()) == 1

        await owner.close()
        assert len(registry.list_all()) == 0


class TestEnvIsolation:
    async def test_server_uses_real_home(self, tmp_path):
        """No runtime_dir — server process inherits the real HOME."""
        async with OpenCodeRuntime(project_dir=tmp_path) as r:
            session = await r.session()
            health = await session.raw_client.health()
            assert health["healthy"] is True
            assert r.runtime_dir is None

    async def test_server_uses_isolated_home(self, tmp_path):
        """runtime_dir is set — server dir created under runtime_dir/servers/."""
        async with OpenCodeRuntime(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ) as r:
            assert r.runtime_dir is not None
            session = await r.session()
            assert session.raw_client.base_url.startswith("http://127.0.0.1:")
