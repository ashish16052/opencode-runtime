"""
Tests for OpenCodeClient against the real opencode server.

Covers: health endpoint, auth enforcement, raw get/post.
No model or agent calls — no API keys required.
"""

import base64

import httpx
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


class TestHealth:
    async def test_health_returns_healthy(self, harness):
        result = await harness._client.health()
        assert result["healthy"] is True

    async def test_health_returns_version(self, harness):
        result = await harness._client.health()
        assert "version" in result
        assert isinstance(result["version"], str)
        assert len(result["version"]) > 0

    async def test_health_without_auth_returns_401(self, harness):
        async with httpx.AsyncClient(base_url=harness._client.base_url) as client:
            response = await client.get("/global/health")
        assert response.status_code == 401

    async def test_health_with_wrong_password_returns_401(self, harness):
        wrong = base64.b64encode(b"opencode:wrongpassword").decode()
        async with httpx.AsyncClient(base_url=harness._client.base_url) as client:
            response = await client.get(
                "/global/health",
                headers={"Authorization": f"Basic {wrong}"},
            )
        assert response.status_code == 401


class TestRawClient:
    async def test_get_session_list(self, harness):
        sessions = await harness._client.get("/session")
        assert isinstance(sessions, list)

    async def test_post_creates_session(self, harness):
        result = await harness._client.post("/session", {})
        assert "id" in result
        assert isinstance(result["id"], str)
