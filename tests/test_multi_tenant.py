"""
Multi-tenant tests against the real opencode binary.

Verifies that different workspace/user/config/materials combinations
produce isolated server processes, and that same combinations reuse
the same server.

No model or agent calls — no API keys required.
"""

import pytest

from opencode_harness import OpenCodeHarness

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def harness(tmp_path):
    h = OpenCodeHarness(
        project_dir=tmp_path,
        runtime_dir=tmp_path / "runtime",
    )
    await h.start()
    yield h
    await h.stop()


class TestWorkspaceIsolation:
    async def test_different_workspace_gets_different_server(self, harness):
        """Two different workspaces → two separate server processes."""
        s1 = await harness.session(workspace="acme")
        s2 = await harness.session(workspace="beta")
        assert s1.raw_client is not s2.raw_client
        assert s1.raw_client.base_url != s2.raw_client.base_url

    async def test_same_workspace_reuses_server(self, harness):
        """Same workspace → same server process reused."""
        s1 = await harness.session(workspace="acme")
        s2 = await harness.session(workspace="acme")
        assert s1.raw_client is s2.raw_client

    async def test_different_user_gets_different_server(self, harness):
        """Same workspace, different user_id → different server."""
        s1 = await harness.session(workspace="acme", user_id="u_1")
        s2 = await harness.session(workspace="acme", user_id="u_2")
        assert s1.raw_client is not s2.raw_client

    async def test_same_workspace_and_user_reuses_server(self, harness):
        """Same workspace + user_id → same server."""
        s1 = await harness.session(workspace="acme", user_id="u_1")
        s2 = await harness.session(workspace="acme", user_id="u_1")
        assert s1.raw_client is s2.raw_client

    async def test_different_config_gets_different_server(self, harness):
        """Same workspace, different config → different server."""
        s1 = await harness.session(workspace="acme", config={"model": "anthropic/claude-haiku-4-5"})
        s2 = await harness.session(
            workspace="acme", config={"model": "anthropic/claude-sonnet-4-5"}
        )
        assert s1.raw_client is not s2.raw_client


class TestServerDirs:
    async def test_each_workspace_gets_own_server_dir(self, harness):
        """Each workspace gets its own subdirectory under runtime_dir/servers/."""
        await harness.session(workspace="acme")
        await harness.session(workspace="beta")
        servers_root = harness.runtime_dir / "servers"
        server_dirs = list(servers_root.iterdir())
        assert len(server_dirs) == 3  # default + acme + beta

    async def test_server_dir_has_log(self, harness):
        """Each server dir contains an opencode.log."""
        await harness.session(workspace="acme")
        servers_root = harness.runtime_dir / "servers"
        for d in servers_root.iterdir():
            assert (d / "opencode.log").exists()


class TestStopBehaviour:
    async def test_stop_all_terminates_all_tenant_servers(self, tmp_path):
        async with OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ) as h:
            await h.session(workspace="acme")
            await h.session(workspace="beta")
            processes = [s.process for s in h._server_manager._servers.values()]
            assert len(processes) == 3  # default + acme + beta

        # All terminated after context manager exit
        assert all(p.returncode is not None for p in processes)

    async def test_stop_single_tenant(self, tmp_path):
        """Stopping one tenant server leaves others running."""
        from opencode_harness.server import _compute_runtime_key
        from pathlib import Path

        async with OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ) as h:
            await h.session(workspace="acme")
            await h.session(workspace="beta")

            acme_key = _compute_runtime_key("acme", None, Path(tmp_path), None, {})
            beta_key = _compute_runtime_key("beta", None, Path(tmp_path), None, {})

            acme_process = h._server_manager._servers[acme_key].process
            await h._server_manager.stop(acme_key)

            assert acme_process.returncode is not None
            assert beta_key in h._server_manager._servers
            assert h._server_manager._servers[beta_key].process.returncode is None
