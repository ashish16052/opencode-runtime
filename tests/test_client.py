"""
Tests for OpenCodeClient against the real opencode server.

Covers: health endpoint, auth enforcement, raw get/post.
No model or agent calls — no API keys required.
"""

import base64

import httpx
import pytest

from opencode_runtime import OpenCodeRuntime

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def runtime(tmp_path):
    h = OpenCodeRuntime(
        project_dir=tmp_path,
        runtime_dir=tmp_path / "runtime",
    )
    yield h
    await h.close()


@pytest.fixture
async def client(runtime):
    session = await runtime.session()
    return session.raw_client


class TestHealth:
    async def test_health_returns_healthy(self, client):
        result = await client.health()
        assert result["healthy"] is True

    async def test_health_returns_version(self, client):
        result = await client.health()
        assert "version" in result
        assert isinstance(result["version"], str)
        assert len(result["version"]) > 0

    async def test_health_without_auth_returns_401(self, client):
        async with httpx.AsyncClient(base_url=client.base_url) as http:
            response = await http.get("/global/health")
        assert response.status_code == 401

    async def test_health_with_wrong_password_returns_401(self, client):
        wrong = base64.b64encode(b"opencode:wrongpassword").decode()
        async with httpx.AsyncClient(base_url=client.base_url) as http:
            response = await http.get(
                "/global/health",
                headers={"Authorization": f"Basic {wrong}"},
            )
        assert response.status_code == 401


class TestRawClient:
    async def test_get_session_list(self, client):
        sessions = await client.get("/session")
        assert isinstance(sessions, list)

    async def test_post_creates_session(self, client):
        result = await client.post("/session", {})
        assert "id" in result
        assert isinstance(result["id"], str)
