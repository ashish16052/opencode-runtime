from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import typing as t
from pathlib import Path

from .server import _find_free_port, _prepare_dir, _terminate_process, _wait_healthy

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
        runtime_dir: str | Path | None = None,
        materials: str | Path | list[str | Path] | None = None,
        config: dict[str, t.Any] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.runtime_dir = Path(runtime_dir).resolve() if runtime_dir else None
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
        if self.runtime_dir is not None:
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
        """Write config overlay and copy materials into runtime_dir.

        Only runs when runtime_dir is set — without it OpenCode discovers
        config from its native XDG paths.
        """
        if self.runtime_dir is None:
            return
        _prepare_dir(self.runtime_dir, self.config, self.materials)

    async def _start_server(self) -> None:
        """Start the opencode server process and initialise the client."""
        from .client import OpenCodeClient
        from .exceptions import OpenCodeNotFoundError

        if shutil.which("opencode") is None:
            raise OpenCodeNotFoundError(
                "opencode binary not found on PATH. Install it with: npm install -g opencode-ai"
            )

        port = _find_free_port()
        host = "127.0.0.1"
        password = secrets.token_urlsafe(32)

        env = {**os.environ, **self.env}
        env["OPENCODE_SERVER_PASSWORD"] = password

        if self.runtime_dir is not None:
            env["HOME"] = str(self.runtime_dir)
            env["TMPDIR"] = str(self.runtime_dir / "tmp")
            env["OPENCODE_CONFIG_HOME"] = str(self.runtime_dir)
            (self.runtime_dir / "tmp").mkdir(parents=True, exist_ok=True)

        if self.runtime_dir is not None:
            log_file = open(self.runtime_dir / "opencode.log", "ab")
            stdout = log_file
            stderr = log_file
        else:
            stdout = asyncio.subprocess.DEVNULL
            stderr = asyncio.subprocess.DEVNULL

        self._process = await asyncio.create_subprocess_exec(
            "opencode",
            "serve",
            "--hostname",
            host,
            "--port",
            str(port),
            cwd=str(self.project_dir),
            env=env,
            stdout=stdout,
            stderr=stderr,
        )

        self._client = OpenCodeClient(
            base_url=f"http://{host}:{port}",
            password=password,
        )

        try:
            await _wait_healthy(self._client)
        except Exception:
            self._process.kill()
            self._process = None
            self._client = None
            raise

    async def _stop_server(self) -> None:
        """Terminate the opencode server process."""
        if self._process is not None:
            await _terminate_process(self._process)
            self._process = None
        self._client = None
