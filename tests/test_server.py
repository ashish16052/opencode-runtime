"""
Tests for internal server helpers — no opencode binary required.
"""

from pathlib import Path

from opencode_harness.server import _ManagedServer, _compute_runtime_key


class TestComputeRuntimeKey:
    def test_same_inputs_produce_same_key(self, tmp_path):
        k1 = _compute_runtime_key("acme", "u_1", tmp_path, None, {})
        k2 = _compute_runtime_key("acme", "u_1", tmp_path, None, {})
        assert k1 == k2

    def test_different_workspace_different_key(self, tmp_path):
        k1 = _compute_runtime_key("acme", "u_1", tmp_path, None, {})
        k2 = _compute_runtime_key("beta", "u_1", tmp_path, None, {})
        assert k1 != k2

    def test_different_user_different_key(self, tmp_path):
        k1 = _compute_runtime_key("acme", "u_1", tmp_path, None, {})
        k2 = _compute_runtime_key("acme", "u_2", tmp_path, None, {})
        assert k1 != k2

    def test_different_config_different_key(self, tmp_path):
        k1 = _compute_runtime_key(None, None, tmp_path, None, {"model": "a"})
        k2 = _compute_runtime_key(None, None, tmp_path, None, {"model": "b"})
        assert k1 != k2

    def test_different_materials_different_key(self, tmp_path):
        k1 = _compute_runtime_key(None, None, tmp_path, "./mat/a", {})
        k2 = _compute_runtime_key(None, None, tmp_path, "./mat/b", {})
        assert k1 != k2

    def test_none_workspace_and_user_stable(self, tmp_path):
        """No workspace/user → still produces a stable key (default server)."""
        k1 = _compute_runtime_key(None, None, tmp_path, None, {})
        k2 = _compute_runtime_key(None, None, tmp_path, None, {})
        assert k1 == k2

    def test_key_is_16_chars(self, tmp_path):
        key = _compute_runtime_key("acme", "u_1", tmp_path, None, {})
        assert len(key) == 16

    def test_key_is_hex(self, tmp_path):
        key = _compute_runtime_key("acme", "u_1", tmp_path, None, {})
        int(key, 16)  # raises if not valid hex


class TestManagedServer:
    def test_fields_accessible(self):
        """_ManagedServer stores key, process, client, server_dir."""
        server = _ManagedServer(
            key="abc123",
            process=None,  # type: ignore[arg-type]
            client=None,  # type: ignore[arg-type]
            server_dir=Path("/tmp/test"),
        )
        assert server.key == "abc123"
        assert server.server_dir == Path("/tmp/test")

    def test_server_dir_can_be_none(self):
        """server_dir is None when no runtime_dir (no isolation)."""
        server = _ManagedServer(
            key="abc123",
            process=None,  # type: ignore[arg-type]
            client=None,  # type: ignore[arg-type]
            server_dir=None,
        )
        assert server.server_dir is None
