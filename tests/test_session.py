"""
Tests for OpenCodeSession against the real opencode server.

Covers: session factory, raw_client wiring, session_id, OpenCode session CRUD.
No model or agent calls — no API keys required.
"""

import pytest

from opencode_runtime import OpenCodeRuntime, OpenCodeSession

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def runtime(tmp_path):
    r = OpenCodeRuntime(
        project_dir=tmp_path,
        runtime_dir=tmp_path / "runtime",
    )
    yield r
    await r.close()


class TestSessionFactory:
    async def test_session_returns_opencodesession(self, runtime):
        session = await runtime.session(workspace="acme", user_id="u_1")
        assert isinstance(session, OpenCodeSession)
        assert session.workspace == "acme"
        assert session.user_id == "u_1"

    async def test_session_id_starts_none(self, runtime):
        """session_id is None until first ask()/stream() creates the server-side session."""
        session = await runtime.session()
        assert session.session_id is None

    async def test_session_resume_id_stored(self, runtime):
        """Passing session_id stores it for resumption — skips POST /session on first use."""
        session = await runtime.session(session_id="ses_abc123")
        assert session.session_id == "ses_abc123"

    async def test_session_raw_client_reachable(self, runtime):
        session = await runtime.session()
        health = await session.raw_client.health()
        assert health["healthy"] is True

    async def test_session_config_merged(self, runtime):
        session = await runtime.session(config={"model": "test/model"})
        assert session.config["model"] == "test/model"

    async def test_session_materials_override_gets_separate_server(self, tmp_path):
        """Per-session materials → different runtime key → different server."""
        mat_dir = tmp_path / "materials"
        mat_dir.mkdir()
        (mat_dir / "AGENTS.md").write_text("# agents")

        async with OpenCodeRuntime(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ) as r:
            s1 = await r.session()
            s2 = await r.session(materials=str(mat_dir))
            # Different materials → different key → different server/client
            assert s1.raw_client.base_url != s2.raw_client.base_url


class TestOpenCodeSession:
    async def test_create_returns_id(self, runtime):
        session = await runtime.session()
        result = await session.raw_client.post("/session", {})
        assert "id" in result
        assert len(result["id"]) > 0

    async def test_create_with_title(self, runtime):
        session = await runtime.session()
        result = await session.raw_client.post("/session", {"title": "my session"})
        assert "id" in result

    async def test_get_by_id(self, runtime):
        session = await runtime.session()
        client = session.raw_client
        created = await client.post("/session", {})
        fetched = await client.get(f"/session/{created['id']}")
        assert fetched["id"] == created["id"]

    async def test_list_includes_created(self, runtime):
        session = await runtime.session()
        client = session.raw_client
        await client.post("/session", {})
        sessions = await client.get("/session")
        assert isinstance(sessions, list)
        assert len(sessions) >= 1
