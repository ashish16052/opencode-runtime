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


class TestSessionFactory:
    async def test_session_returns_opencodesession(self, harness):
        session = await harness.session(workspace="acme", user_id="u_1")
        assert isinstance(session, OpenCodeSession)
        assert session.workspace == "acme"
        assert session.user_id == "u_1"

    async def test_session_correlation_id(self, harness):
        session = await harness.session(session_id="chat_456")
        assert session.session_id == "chat_456"

    async def test_session_raw_client_is_harness_client(self, harness):
        session = await harness.session()
        assert session.raw_client is harness._client

    async def test_session_oc_session_id_starts_none(self, harness):
        session = await harness.session()
        assert session._oc_session_id is None

    async def test_session_config_merged(self, harness):
        session = await harness.session(config={"model": "test/model"})
        assert session.config["model"] == "test/model"


class TestOpenCodeSession:
    async def test_create_returns_id(self, harness):
        result = await harness._client.post("/session", {})
        assert "id" in result
        assert len(result["id"]) > 0

    async def test_create_with_title(self, harness):
        result = await harness._client.post("/session", {"title": "my session"})
        assert "id" in result

    async def test_get_by_id(self, harness):
        created = await harness._client.post("/session", {})
        fetched = await harness._client.get(f"/session/{created['id']}")
        assert fetched["id"] == created["id"]

    async def test_list_includes_created(self, harness):
        await harness._client.post("/session", {})
        sessions = await harness._client.get("/session")
        assert isinstance(sessions, list)
        assert len(sessions) >= 1
