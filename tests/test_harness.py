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


class TestHarnessLifecycle:
    async def test_start_creates_runtime_dir(self, tmp_path):
        h = OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        )
        await h.start()
        assert h.runtime_dir.exists()
        await h.stop()

    async def test_process_running_after_start(self, harness):
        assert harness._process is not None
        assert harness._process.returncode is None  # still running

    async def test_client_set_after_start(self, harness):
        assert harness._client is not None
        assert harness._client.base_url.startswith("http://127.0.0.1:")

    async def test_process_gone_after_stop(self, tmp_path):
        h = OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        )
        await h.start()
        process = h._process
        assert process is not None
        await h.stop()
        assert h._process is None
        assert h._client is None
        assert process.returncode is not None

    async def test_context_manager(self, tmp_path):
        async with OpenCodeHarness(
            project_dir=tmp_path,
            runtime_dir=tmp_path / "runtime",
        ) as h:
            assert h._process is not None
            assert h._client is not None
        assert h._process is None
        assert h._client is None
