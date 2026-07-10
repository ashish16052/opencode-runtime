"""
Internal server lifecycle helpers.

All symbols in this module are private to opencode-runtime.
Nothing here is exported in __all__.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from importlib.metadata import version
except ImportError:
    from importlib_metadata import version  # type: ignore[import-not-found,no-redef]

import httpx

from . import registry
from .client import OpenCodeClient
from .exceptions import (
    OpenCodeNotFoundError,
    OpenCodeRuntimeError,
    OpenCodeServerError,
    OpenCodeTimeoutError,
)
from .registry import RegistryEntry, ServerState

# One or more paths to overlay into the server dir before startup.
# Module-level alias: inside ServerManager's class body, `list[...]` in an
# annotation would resolve to the ServerManager.list method, not the builtin.
Materials = str | Path | list[str | Path] | None


@dataclass
class _ManagedServer:
    """A running opencode server process tracked by the runtime."""

    key: str
    process: asyncio.subprocess.Process | None  # set during _start(); None from get_or_start()
    client: OpenCodeClient
    server_dir: Path | None  # None when runtime_dir is not set (no isolation)


def _is_process_alive(pid: int | None) -> bool:
    """Return True if pid is set and a process with it is running."""
    return registry.is_alive(pid)


async def _is_health_ok(client: OpenCodeClient, timeout: float = 3.0) -> bool:
    """Return True if /global/health endpoint responds successfully."""
    try:
        await asyncio.wait_for(client.health(), timeout=timeout)
        return True
    except Exception:
        return False


def _compute_display_status(
    state: ServerState, process_alive: bool, health_ok: bool, lease_expired: bool = False
) -> str:
    """Derive user-facing display status from state, process liveness, and health.

    Returns one of: starting, running, unhealthy, stale, failed.
    """
    if state == ServerState.STARTING:
        return "failed" if lease_expired else "starting"
    if state == ServerState.STOPPING:
        return "stopping"
    if state == ServerState.FAILED:
        return "failed"
    if not process_alive:
        return "stale"
    if not health_ok:
        return "unhealthy"
    return "running"


def _find_free_port(host: str = "127.0.0.1") -> int:
    """Bind to port 0 and let the OS pick a free ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


async def _wait_healthy(
    client: OpenCodeClient,
    timeout: float = 60.0,
    process: asyncio.subprocess.Process | None = None,
) -> None:
    """Poll GET /global/health until the server responds or timeout expires."""
    deadline = asyncio.get_event_loop().time() + timeout
    last_exc: Exception | None = None

    while asyncio.get_event_loop().time() < deadline:
        if process is not None and process.returncode is not None:
            raise OpenCodeTimeoutError(f"opencode process exited with code {process.returncode}")
        try:
            await client.health()
            return
        except Exception as exc:
            last_exc = exc
            await asyncio.sleep(1.0)

    raise OpenCodeTimeoutError(
        f"opencode server did not become healthy within {timeout}s (last error: {last_exc})"
    )


def _prepare_dir(
    server_dir: Path,
    config: dict[str, Any],
    materials: Materials,
) -> None:
    """Write opencode.json and overlay materials into server_dir."""
    if config:
        (server_dir / "opencode.json").write_text(
            json.dumps(config, indent=2),
            encoding="utf-8",
        )

    if materials is not None:
        paths = materials if isinstance(materials, list) else [materials]
        for src in paths:
            src = Path(src).resolve()
            if not src.exists():
                raise OpenCodeRuntimeError(f"materials path does not exist: {src}")
            if src.is_dir():
                for item in src.iterdir():
                    dest = server_dir / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)
            else:
                shutil.copy2(src, server_dir / src.name)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """Terminate a process gracefully, kill if it doesn't exit within 5s."""
    if process.returncode is not None:
        return  # already exited
    try:
        process.terminate()
    except ProcessLookupError:
        return  # already dead
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass


def _compute_runtime_key(
    workspace: str | None,
    user_id: str | None,
    project_dir: Path,
    materials: Materials,
    config: dict[str, Any],
) -> str:
    """Compute a stable 16-char key for a unique server configuration.

    Same inputs always produce the same key. Different inputs (different
    workspace, user, materials, or config) produce different keys and
    therefore get separate server processes.
    """
    payload = "|".join(
        [
            workspace or "",
            user_id or "",
            str(project_dir),
            repr(
                sorted(
                    str(m)
                    for m in (materials if isinstance(materials, list) else [materials or ""])
                )
            ),
            json.dumps(config, sort_keys=True, default=str),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class ServerManager:
    """Manages a pool of OpenCode instance processes.

    Each unique combination of workspace, user_id, project_dir, materials,
    and config gets its own isolated OpenCode instance. Instances are started
    on demand and reused when the same key is requested again.

    The registry is the single source of truth for server state — there is
    no in-memory cache of it, so every call consults the registry and
    external actors (CLI stop-all, another process) are always reflected
    immediately. The one piece of instance state this class keeps is
    _owned — which keys *this instance* actually spawned via get_or_start(),
    as opposed to attaching to a server already running elsewhere — so that
    callers like OpenCodeRuntime.close() can stop what they started without
    touching servers owned by someone else.
    """

    def __init__(self) -> None:
        self._owned: set[str] = set()

    def find(self, key: str) -> RegistryEntry | None:
        """Return the registry entry for key, or None if not found or still starting."""
        entry = registry.read(key)
        return entry if entry is not None and entry.state == ServerState.RUNNING else None

    def is_alive(self, key: str) -> bool:
        """Return True if the server for key is running."""
        entry = self.find(key)
        return entry is not None and registry.is_alive(entry.pid)

    def touch(self, key: str) -> None:
        """Update last_used_at timestamp for a server. Call after session creation."""
        entry = registry.read(key)
        if entry is not None and entry.state == ServerState.RUNNING:
            entry.last_used_at = registry.now_iso()
            registry.write(entry)

    def list(self) -> list[tuple[RegistryEntry, bool]]:
        """Return all ready registry entries with their liveness status."""
        return [
            (entry, registry.is_alive(entry.pid))
            for entry in registry.list_all()
            if entry.state == ServerState.RUNNING
        ]

    async def health(self, key: str) -> dict[str, Any]:
        """Return health info for the server at key.

        Raises ``OpenCodeServerError`` if key is not in the registry or the
        server is unreachable.
        """
        entry = self.find(key)
        if entry is None:
            raise OpenCodeServerError(f"server {key!r} not found in registry")

        client = OpenCodeClient(
            base_url=f"http://127.0.0.1:{entry.port}",
            password=entry.password,
        )
        try:
            return await client.health()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise OpenCodeServerError(f"server {key!r} unreachable: {exc}") from exc

    async def stop(self, key: str) -> bool:
        """Kill the server for key and remove its registry entry.

        Returns True if the process was alive and killed, False if it was
        already dead or not found in the registry.
        """
        entry = registry.read(key)
        if entry is None:
            return False

        registry.delete(key)

        if entry.pid is None or not registry.is_alive(entry.pid):
            return False

        try:
            os.kill(entry.pid, signal.SIGTERM)
        except ProcessLookupError:
            return False
        # Wait for the process to exit (up to 5s, then SIGKILL).
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            if not registry.is_alive(entry.pid):
                return True
            await asyncio.sleep(0.1)
        try:
            os.kill(entry.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return True

    async def stop_all(self) -> None:
        """Kill all servers tracked in the registry."""
        for entry in registry.list_all():
            await self.stop(entry.key)

    async def stop_owned(self) -> None:
        """Stop every server this instance itself spawned via get_or_start().

        Servers this instance merely attached to — already running, started
        by another process or the CLI — are left alone.
        """
        for key in list(self._owned):
            await self.stop(key)
        self._owned.clear()

    async def get_or_start(
        self,
        *,
        key: str,
        project_dir: Path,
        server_dir: Path | None,
        materials: Materials,
        config: dict[str, Any],
        env: dict[str, str],
        workspace: str | None = None,
        user_id: str | None = None,
    ) -> _ManagedServer:
        """Return a client for the running server, starting one if needed."""
        entry = registry.read(key)
        if entry is not None and entry.state == ServerState.RUNNING:
            if registry.is_alive(entry.pid):
                return self._attach(entry)
            # Stale — the process died after finishing startup.
            registry.delete(key)

        # port/password are generated here (not inside _start) because a
        # 'starting' claim row needs both up front — pid is the only field
        # that isn't known until the subprocess actually exists.
        port = _find_free_port()
        password = secrets.token_urlsafe(32)
        timestamp = registry.now_iso()

        claimed = registry.claim_starting(
            RegistryEntry(
                key=key,
                state=ServerState.STARTING,
                pid=None,
                port=port,
                password=password,
                project_dir=str(project_dir),
                server_dir=str(server_dir) if server_dir else None,
                started_at=timestamp,
                claimed_at=timestamp,
                workspace=workspace,
                user_id=user_id,
            )
        )
        if not claimed:
            # Another caller is already starting this key — wait for it
            # instead of racing to spawn a second process for the same key.
            return await self._wait_for_ready(key)

        try:
            server = await self._start(
                key=key,
                project_dir=project_dir,
                server_dir=server_dir,
                materials=materials,
                config=config,
                env=env,
                port=port,
                password=password,
                workspace=workspace,
                user_id=user_id,
            )
        except Exception:
            registry.delete(key)
            raise
        self._owned.add(key)
        return server

    def _attach(self, entry: RegistryEntry) -> _ManagedServer:
        """Build a _ManagedServer client for an already-running registry entry."""
        return _ManagedServer(
            key=entry.key,
            process=None,
            client=OpenCodeClient(
                base_url=f"http://127.0.0.1:{entry.port}",
                password=entry.password,
            ),
            server_dir=Path(entry.server_dir) if entry.server_dir else None,
        )

    async def _wait_for_ready(self, key: str, timeout: float = 60.0) -> _ManagedServer:
        """Poll the registry until the caller holding the start-claim finishes."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            entry = registry.read(key)
            if entry is None:
                raise OpenCodeServerError(
                    f"the caller starting server {key!r} failed before it became ready"
                )
            if entry.state == ServerState.RUNNING and registry.is_alive(entry.pid):
                return self._attach(entry)
            await asyncio.sleep(0.1)

        raise OpenCodeTimeoutError(
            f"timed out waiting for another caller to start the server for key {key!r}"
        )

    async def _start(
        self,
        *,
        key: str,
        project_dir: Path,
        server_dir: Path | None,
        materials: Materials,
        config: dict[str, Any],
        env: dict[str, str],
        port: int,
        password: str,
        workspace: str | None = None,
        user_id: str | None = None,
    ) -> _ManagedServer:
        """Start a new OpenCode instance and return a _ManagedServer."""
        if shutil.which("opencode") is None:
            raise OpenCodeNotFoundError(
                "opencode binary not found on PATH. Install it with: npm install -g opencode-ai"
            )

        process_env = {**os.environ, **env}
        process_env["OPENCODE_SERVER_PASSWORD"] = password

        if server_dir is not None:
            server_dir.mkdir(parents=True, exist_ok=True)
            (server_dir / "tmp").mkdir(exist_ok=True)
            _prepare_dir(server_dir, config, materials)
            process_env["HOME"] = str(server_dir)
            process_env["TMPDIR"] = str(server_dir / "tmp")
            process_env["OPENCODE_CONFIG"] = str(server_dir / "opencode.json")
            output: Any = open(server_dir / "opencode.log", "ab")
        else:
            output = asyncio.subprocess.DEVNULL

        process = await asyncio.create_subprocess_exec(
            "opencode",
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
            cwd=str(project_dir),
            env=process_env,
            stdout=output,
            stderr=output,
        )

        client = OpenCodeClient(
            base_url=f"http://127.0.0.1:{port}",
            password=password,
        )

        # Use a short per-request timeout while polling — avoids a single
        # hanging request consuming the entire startup budget.
        poll_client = OpenCodeClient(
            base_url=f"http://127.0.0.1:{port}",
            password=password,
            timeout=3.0,
        )

        try:
            await _wait_healthy(poll_client, process=process)
        except Exception:
            await _terminate_process(process)
            raise

        timestamp = registry.now_iso()
        registry.write(
            RegistryEntry(
                key=key,
                state=ServerState.RUNNING,
                pid=process.pid,
                port=port,
                password=password,
                project_dir=str(project_dir),
                server_dir=str(server_dir) if server_dir else None,
                started_at=timestamp,
                claimed_at=timestamp,
                last_used_at=timestamp,
                runtime_version=version("opencode-runtime"),
                workspace=workspace,
                user_id=user_id,
            )
        )

        return _ManagedServer(
            key=key,
            process=process,
            client=client,
            server_dir=server_dir,
        )
