"""
Multi-tenant tests against the real opencode binary.

Verifies that different workspace/user/config/materials combinations
produce isolated server processes, and that same combinations reuse
the same server.

No model or agent calls — no API keys required.
"""

import pytest

import opencode_harness.registry as registry
from opencode_harness import OpenCodeHarness

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
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
        assert s1.raw_client.base_url != s2.raw_client.base_url

    async def test_same_workspace_reuses_server(self, harness):
        """Same workspace → same server (same port)."""
        s1 = await harness.session(workspace="acme")
        s2 = await harness.session(workspace="acme")
        assert s1.raw_client.base_url == s2.raw_client.base_url

    async def test_different_user_gets_different_server(self, harness):
        """Same workspace, different user_id → different server."""
        s1 = await harness.session(workspace="acme", user_id="u_1")
        s2 = await harness.session(workspace="acme", user_id="u_2")
        assert s1.raw_client.base_url != s2.raw_client.base_url

    async def test_same_workspace_and_user_reuses_server(self, harness):
        """Same workspace + user_id → same server."""
        s1 = await harness.session(workspace="acme", user_id="u_1")
        s2 = await harness.session(workspace="acme", user_id="u_1")
        assert s1.raw_client.base_url == s2.raw_client.base_url

    async def test_different_config_gets_different_server(self, harness):
        """Same workspace, different config → different server."""
        s1 = await harness.session(workspace="acme", config={"model": "anthropic/claude-haiku-4-5"})
        s2 = await harness.session(
            workspace="acme", config={"model": "anthropic/claude-sonnet-4-5"}
        )
        assert s1.raw_client.base_url != s2.raw_client.base_url


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
    async def test_stop_all_terminates_all_tenant_servers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        async with OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ) as h:
            await h.session(workspace="acme")
            await h.session(workspace="beta")
            entries = registry.list_all()
            assert len(entries) == 3  # default + acme + beta
            pids = [e.pid for e in entries]

        # All terminated after context manager exit
        assert all(not registry.is_alive(pid) for pid in pids)
        assert registry.list_all() == []

    async def test_stop_single_tenant(self, tmp_path, monkeypatch):
        """Stopping one tenant server leaves others running."""
        from pathlib import Path

        from opencode_harness.server import _compute_runtime_key

        monkeypatch.setattr(registry, "REGISTRY_DIR", tmp_path / "reg")
        async with OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ) as h:
            await h.session(workspace="acme")
            await h.session(workspace="beta")

            acme_key = _compute_runtime_key("acme", None, Path(tmp_path), None, {})
            beta_key = _compute_runtime_key("beta", None, Path(tmp_path), None, {})

            acme_entry = registry.read(acme_key)
            beta_entry = registry.read(beta_key)
            assert acme_entry is not None
            assert beta_entry is not None

            await h._server_manager.stop(acme_key)

            assert registry.read(acme_key) is None
            assert not registry.is_alive(acme_entry.pid)
            assert registry.read(beta_key) is not None
            assert registry.is_alive(beta_entry.pid)
