"""
Multi-tenant tests against the real opencode binary.

Verifies that different workspace/user/config/materials combinations
produce isolated server processes, and that same combinations reuse
the same server.

No model or agent calls — no API keys required.
"""

import pytest

from opencode_runtime import OpenCodeRuntime, process, registry

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
    r = OpenCodeRuntime(
        project_dir=tmp_path,
        runtime_dir=tmp_path / "runtime",
    )
    yield r
    await r.close()


class TestWorkspaceIsolation:
    async def test_different_workspace_gets_different_server(self, runtime):
        """Two different workspaces → two separate server processes."""
        s1 = await runtime.session(workspace="acme")
        s2 = await runtime.session(workspace="beta")
        assert s1.raw_client.base_url != s2.raw_client.base_url

    async def test_same_workspace_reuses_server(self, runtime):
        """Same workspace → same server (same port)."""
        s1 = await runtime.session(workspace="acme")
        s2 = await runtime.session(workspace="acme")
        assert s1.raw_client.base_url == s2.raw_client.base_url

    async def test_different_user_gets_different_server(self, runtime):
        """Same workspace, different user_id → different server."""
        s1 = await runtime.session(workspace="acme", user_id="u_1")
        s2 = await runtime.session(workspace="acme", user_id="u_2")
        assert s1.raw_client.base_url != s2.raw_client.base_url

    async def test_same_workspace_and_user_reuses_server(self, runtime):
        """Same workspace + user_id → same server."""
        s1 = await runtime.session(workspace="acme", user_id="u_1")
        s2 = await runtime.session(workspace="acme", user_id="u_1")
        assert s1.raw_client.base_url == s2.raw_client.base_url

    async def test_different_config_gets_different_server(self, runtime):
        """Same workspace, different config → different server."""
        s1 = await runtime.session(workspace="acme", config={"model": "anthropic/claude-haiku-4-5"})
        s2 = await runtime.session(
            workspace="acme", config={"model": "anthropic/claude-sonnet-4-5"}
        )
        assert s1.raw_client.base_url != s2.raw_client.base_url


class TestServerDirs:
    async def test_each_workspace_gets_own_server_dir(self, runtime):
        """Each workspace gets its own subdirectory under runtime_dir/servers/."""
        await runtime.session(workspace="acme")
        await runtime.session(workspace="beta")
        servers_root = runtime.runtime_dir / "servers"
        server_dirs = list(servers_root.iterdir())
        assert len(server_dirs) == 2  # acme + beta

    async def test_server_dir_has_log(self, runtime):
        """Each server dir contains an opencode.log."""
        await runtime.session(workspace="acme")
        servers_root = runtime.runtime_dir / "servers"
        for d in servers_root.iterdir():
            assert (d / "opencode.log").exists()


class TestStopBehaviour:
    async def test_stop_single_tenant(self, tmp_path, monkeypatch):
        """Stopping one tenant server leaves others running."""
        from pathlib import Path

        from opencode_runtime.server import _compute_runtime_key

        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        async with OpenCodeRuntime(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ) as r:
            await r.session(workspace="acme")
            await r.session(workspace="beta")

            acme_key = _compute_runtime_key("acme", None, Path(tmp_path), None, {})
            beta_key = _compute_runtime_key("beta", None, Path(tmp_path), None, {})

            acme_entry = registry.read(acme_key)
            beta_entry = registry.read(beta_key)
            assert acme_entry is not None
            assert beta_entry is not None

            await r._server_manager.stop(acme_key)

            assert registry.read(acme_key) is None
            assert not process.is_alive(acme_entry.pid)
            assert registry.read(beta_key) is not None
            assert process.is_alive(beta_entry.pid)
