"""
Tests for OpenCodeSession against the real opencode server.

Covers: session factory, raw_client wiring, oc_session_id, OpenCode session CRUD.
No model or agent calls — no API keys required.
"""

import pytest

from opencode_harness import OpenCodeHarness, OpenCodeSession

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


def _client(h: OpenCodeHarness):
    """Return the client from the first (default) server."""
    return list(h._server_manager._servers.values())[0].client


class TestSessionFactory:
    async def test_session_returns_opencodesession(self, harness):
        session = await harness.session(workspace="acme", user_id="u_1")
        assert isinstance(session, OpenCodeSession)
        assert session.workspace == "acme"
        assert session.user_id == "u_1"

    async def test_session_correlation_id(self, harness):
        session = await harness.session(session_id="chat_456")
        assert session.session_id == "chat_456"

    async def test_session_raw_client_is_server_client(self, harness):
        session = await harness.session()
        assert session.raw_client is _client(harness)

    async def test_session_oc_session_id_starts_none(self, harness):
        session = await harness.session()
        assert session._oc_session_id is None

    async def test_session_config_merged(self, harness):
        session = await harness.session(config={"model": "test/model"})
        assert session.config["model"] == "test/model"

    async def test_session_materials_override_gets_separate_server(self, tmp_path):
        """Per-session materials → different runtime key → different server."""
        mat_dir = tmp_path / "materials"
        mat_dir.mkdir()
        (mat_dir / "AGENTS.md").write_text("# agents")

        async with OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ) as h:
            s1 = await h.session()
            s2 = await h.session(materials=str(mat_dir))
            # Different materials → different key → different server/client
            assert s1.raw_client is not s2.raw_client


class TestOpenCodeSession:
    async def test_create_returns_id(self, harness):
        result = await _client(harness).post("/session", {})
        assert "id" in result
        assert len(result["id"]) > 0

    async def test_create_with_title(self, harness):
        result = await _client(harness).post("/session", {"title": "my session"})
        assert "id" in result

    async def test_get_by_id(self, harness):
        client = _client(harness)
        created = await client.post("/session", {})
        fetched = await client.get(f"/session/{created['id']}")
        assert fetched["id"] == created["id"]

    async def test_list_includes_created(self, harness):
        client = _client(harness)
        await client.post("/session", {})
        sessions = await client.get("/session")
        assert isinstance(sessions, list)
        assert len(sessions) >= 1
