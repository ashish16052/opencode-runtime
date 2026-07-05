"""
Tests for OpenCodeHarness lifecycle against the real opencode binary.

Covers: start / stop, process management, context manager, runtime dir.
No model or agent calls — no API keys required.
"""

import pytest

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
