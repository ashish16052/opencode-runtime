from __future__ import annotations

import typing as t
from pathlib import Path

from .server import ServerManager, _compute_runtime_key

if t.TYPE_CHECKING:
    from .session import OpenCodeSession


class OpenCodeRuntime:
    """Lifecycle manager and session factory for OpenCode Runtime.

    ``OpenCodeRuntime`` is the entry point for deploying OpenCode in a
    multi-user backend. It manages isolated workspace environments,
    OpenCode instance lifecycles, and session routing so that each user
    gets a fully isolated runtime without any shared state.

    Args:
        project_dir:  The project directory OpenCode should run against.
        runtime_dir:  Where OpenCode Runtime stores managed workspace state.
                      When set, each session gets an isolated OpenCode instance
                      with its own HOME, config, and conversation history under
                      ``runtime_dir/servers/<key>/``.
                      When not set, OpenCode runs with the user's real
                      environment and discovers config normally.
        materials:    OpenCode-native files to overlay into the runtime
                      workspace. Applied to every session unless overridden
                      per-session.
        config:       Raw OpenCode config dict. Merged with per-session
                      config (session config takes precedence).
        env:          Extra environment variables passed to the OpenCode
                      instance process.
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

    async def __aenter__(self) -> OpenCodeRuntime:
        await self.start()
        return self

    async def __aexit__(self, exc_type: t.Any, exc: t.Any, tb: t.Any) -> None:
        await self.stop()

    async def start(self) -> None:
        """Start the default OpenCode instance eagerly so the runtime is ready to use."""
        await self.session()

    async def stop(self) -> None:
        """Shut down all managed OpenCode instance processes."""
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
        materials: str | Path | list[str | Path] | None = None,
        config: dict[str, t.Any] | None = None,
        env: dict[str, str] | None = None,
    ) -> OpenCodeSession:
        """Create a session backed by this runtime.

        Each unique combination of workspace, user_id, materials, and config
        maps to a dedicated OpenCode instance process. The instance is started
        on first use and reused for subsequent sessions with the same key.

        Args:
            workspace:   Logical tenant/workspace name, e.g. ``"acme"``.
            user_id:     Application user id, e.g. ``"u_123"``.
            session_id:  OpenCode server-side session ID. Pass an existing ID to
                         resume a previous conversation; omit to start a new one.
            materials:   Per-session materials override. Falls back to
                         runtime-level materials when not set.
            config:      Merged on top of runtime-level config.
            env:         Merged on top of runtime-level env.
        """
        from .session import OpenCodeSession

        effective_materials = materials if materials is not None else self.materials
        effective_config = {**self.config, **(config or {})}
        effective_env = {**self.env, **(env or {})}

        key = _compute_runtime_key(
            workspace,
            user_id,
            self.project_dir,
            effective_materials,
            effective_config,
        )

        server_dir = self.runtime_dir / "servers" / key if self.runtime_dir is not None else None

        server = await self._server_manager.get_or_start(
            key=key,
            project_dir=self.project_dir,
            server_dir=server_dir,
            materials=effective_materials,
            config=effective_config,
            env=effective_env,
            workspace=workspace,
            user_id=user_id,
        )

        return OpenCodeSession(
            client=server.client,
            workspace=workspace,
            user_id=user_id,
            session_id=session_id,
            config=effective_config,
            env=effective_env,
        )
