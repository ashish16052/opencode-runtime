from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import socket
import typing as t
from pathlib import Path

if t.TYPE_CHECKING:
    from .client import OpenCodeClient
    from .session import OpenCodeSession


class OpenCodeHarness:
    """Lifecycle manager and session factory for opencode-harness.

    ``OpenCodeHarness`` is the single owner of runtime state: the server
    process, the HTTP client, runtime directory layout, config, and materials.
    Sessions are lightweight wrappers that delegate back to the harness.

    Args:
        project_dir:  The project directory OpenCode should run against.
        runtime_dir:  Where opencode-harness stores managed runtime state.
        materials:    OpenCode-native files to overlay into the runtime
                      workspace. Applied to every session unless overridden.
        config:       Raw OpenCode config dict. Merged with per-session config
                      (session config takes precedence).
        env:          Extra environment variables passed to the opencode server
                      process. Merged with per-session env overrides.
    """

    def __init__(
        self,
        *,
        project_dir: str | Path = ".",
        runtime_dir: str | Path = ".opencode-harness",
        materials: str | Path | list[str | Path] | None = None,
        config: dict[str, t.Any] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.runtime_dir = Path(runtime_dir).resolve()
        self.materials = materials
        self.config = config or {}
        self.env = env or {}

        self._client: OpenCodeClient | None = None  # set in _start_server
        self._process = None  # asyncio.subprocess.Process, set in _start_server

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> OpenCodeHarness:
        await self.start()
        return self

    async def __aexit__(self, exc_type: t.Any, exc: t.Any, tb: t.Any) -> None:
        await self.stop()

    async def start(self) -> None:
        """Prepare the runtime directory and start the opencode server."""
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        await self._prepare_runtime()
        await self._start_server()

    async def stop(self) -> None:
        """Shut down the managed opencode server."""
        await self._stop_server()

    # ------------------------------------------------------------------
    # Session factory
    # ------------------------------------------------------------------

    async def session(
        self,
        *,
        workspace: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        config: dict[str, t.Any] | None = None,
        env: dict[str, str] | None = None,
    ) -> OpenCodeSession:
        """Create a session backed by this harness.

        Args:
            workspace:   Logical tenant/workspace name, e.g. ``"acme"``.
            user_id:     Application user id, e.g. ``"u_123"``.
            session_id:  External correlation id stored in metadata. OpenCode
                         generates its own internal session id server-side.
            config:      Merged on top of harness-level config.
            env:         Merged on top of harness-level env.
        """
        from .session import OpenCodeSession

        return OpenCodeSession(
            harness=self,
            workspace=workspace,
            user_id=user_id,
            session_id=session_id,
            config={**self.config, **(config or {})},
            env={**self.env, **(env or {})},
        )

    # ------------------------------------------------------------------
    # Internal — runtime preparation (extracted later when complex)
    # ------------------------------------------------------------------

    async def _prepare_runtime(self) -> None:
        """Write config overlay and copy materials into runtime_dir."""
        # Write opencode.json if config was provided
        if self.config:
            config_path = self.runtime_dir / "opencode.json"
            config_path.write_text(
                json.dumps(self.config, indent=2),
                encoding="utf-8",
            )

        # Overlay materials into runtime_dir
        if self.materials is not None:
            paths = self.materials if isinstance(self.materials, list) else [self.materials]
            for src in paths:
                src = Path(src).resolve()
                if not src.exists():
                    from .exceptions import OpenCodeHarnessError

                    raise OpenCodeHarnessError(f"materials path does not exist: {src}")
                if src.is_dir():
                    for item in src.iterdir():
                        dest = self.runtime_dir / item.name
                        if item.is_dir():
                            shutil.copytree(item, dest, dirs_exist_ok=True)
                        else:
                            shutil.copy2(item, dest)
                else:
                    shutil.copy2(src, self.runtime_dir / src.name)

    async def _start_server(self) -> None:
        """Start the opencode server process and initialise the client."""
        from .client import OpenCodeClient
        from .exceptions import OpenCodeNotFoundError, OpenCodeTimeoutError

        if shutil.which("opencode") is None:
            raise OpenCodeNotFoundError(
                "opencode binary not found on PATH. Install it with: npm install -g opencode-ai"
            )

        # Find a free port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        host = "127.0.0.1"
        password = secrets.token_urlsafe(32)

        # Build isolated environment
        env = {
            **os.environ,
            "HOME": str(self.runtime_dir),
            "TMPDIR": str(self.runtime_dir / "tmp"),
            "OPENCODE_CONFIG_HOME": str(self.runtime_dir),
            "OPENCODE_SERVER_PASSWORD": password,
            **self.env,
        }

        # Ensure tmp dir exists
        (self.runtime_dir / "tmp").mkdir(parents=True, exist_ok=True)

        # Open log file
        log_path = self.runtime_dir / "opencode.log"
        log_file = open(log_path, "ab")

        self._process = await asyncio.create_subprocess_exec(
            "opencode",
            "serve",
            "--hostname",
            host,
            "--port",
            str(port),
            cwd=str(self.project_dir),
            env=env,
            stdout=log_file,
            stderr=log_file,
        )

        self._client = OpenCodeClient(
            base_url=f"http://{host}:{port}",
            password=password,
        )

        # Health check — retry for up to 20 seconds
        deadline = asyncio.get_event_loop().time() + 20.0
        last_exc: Exception | None = None
        while asyncio.get_event_loop().time() < deadline:
            try:
                await self._client.health()
                return  # server is up
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(0.25)

        # Timed out — kill the process and raise
        self._process.kill()
        self._process = None
        self._client = None
        raise OpenCodeTimeoutError(
            f"opencode server did not become healthy within 20s (last error: {last_exc})"
        )

    async def _stop_server(self) -> None:
        """Terminate the opencode server process."""
        if self._process is not None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None
        self._client = None
