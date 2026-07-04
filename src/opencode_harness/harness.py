from __future__ import annotations

import json
import shutil
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
        pass

    async def _stop_server(self) -> None:
        """Terminate the opencode server process."""
        if self._process is not None:
            self._process.terminate()
            self._process = None
        self._client = None
