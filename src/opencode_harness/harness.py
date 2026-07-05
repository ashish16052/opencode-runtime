from __future__ import annotations

import typing as t
from pathlib import Path

from .server import ServerManager, _compute_runtime_key

if t.TYPE_CHECKING:
    from .session import OpenCodeSession


class OpenCodeHarness:
    """Lifecycle manager and session factory for opencode-harness.

    ``OpenCodeHarness`` owns the public API: it accepts configuration,
    manages the server lifecycle via ``ServerManager``, and produces
    ``OpenCodeSession`` objects. All process and runtime concerns are
    delegated to ``ServerManager``.

    Args:
        project_dir:  The project directory OpenCode should run against.
        runtime_dir:  Where opencode-harness stores managed runtime state.
                      When set, each session gets an isolated server with
                      its own HOME, config, and materials under
                      ``runtime_dir/servers/<key>/``.
                      When not set, OpenCode runs with the user's real
                      environment and discovers config normally.
        materials:    OpenCode-native files to overlay into the runtime
                      workspace. Applied to every session unless overridden
                      per-session.
        config:       Raw OpenCode config dict. Merged with per-session
                      config (session config takes precedence).
        env:          Extra environment variables passed to the opencode
                      server process.
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
        self._server_manager = ServerManager()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> OpenCodeHarness:
        await self.start()
        return self

    async def __aexit__(self, exc_type: t.Any, exc: t.Any, tb: t.Any) -> None:
        await self.stop()

    async def start(self) -> None:
        """Start the default server eagerly so the harness is ready to use."""
        await self.session()

    async def stop(self) -> None:
        """Shut down all managed opencode server processes."""
        await self._server_manager.stop_all()

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

        Each unique combination of workspace, user_id, materials, and config
        maps to a dedicated opencode server process. The server is started on
        first use and reused for subsequent sessions with the same key.

        Args:
            workspace:   Logical tenant/workspace name, e.g. ``"acme"``.
            user_id:     Application user id, e.g. ``"u_123"``.
            session_id:  External correlation id stored in metadata. OpenCode
                         generates its own internal session id server-side.
            config:      Merged on top of harness-level config.
            env:         Merged on top of harness-level env.
        """
        from .session import OpenCodeSession

        effective_config = {**self.config, **(config or {})}
        effective_env = {**self.env, **(env or {})}

        key = _compute_runtime_key(
            workspace,
            user_id,
            self.project_dir,
            self.materials,
            effective_config,
        )

        server_dir = self.runtime_dir / "servers" / key if self.runtime_dir is not None else None

        server = await self._server_manager.get_or_start(
            key=key,
            project_dir=self.project_dir,
            server_dir=server_dir,
            materials=self.materials,
            config=effective_config,
            env=effective_env,
        )

        return OpenCodeSession(
            client=server.client,
            workspace=workspace,
            user_id=user_id,
            session_id=session_id,
            config=effective_config,
            env=effective_env,
        )
