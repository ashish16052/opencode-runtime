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
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

try:
    from importlib.metadata import version
except ImportError:
    from importlib_metadata import version  # type: ignore[import-not-found,no-redef]

import httpx

from . import process, registry
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


class DisplayStatus(str, Enum):
    """User-facing status derived from registry state, process liveness, and health."""

    STARTING = "starting"
    RUNNING = "running"
    UNHEALTHY = "unhealthy"
    STALE = "stale"
    FAILED = "failed"
    STOPPING = "stopping"


@dataclass
class ServerStatus:
    """Computed liveness/health status for a registry entry."""

    entry: RegistryEntry
    process_alive: bool
    health_ok: bool
    display: DisplayStatus


async def _is_health_ok(client: OpenCodeClient, timeout: float = 3.0) -> bool:
    """Return True if /global/health endpoint responds successfully."""
    try:
        await asyncio.wait_for(client.health(), timeout=timeout)
        return True
    except Exception:
        return False


def _compute_display_status(
    state: ServerState, process_alive: bool, health_ok: bool, lease_expired: bool = False
) -> DisplayStatus:
    """Derive user-facing display status from state, process liveness, and health."""
    if state == ServerState.STARTING:
        return DisplayStatus.FAILED if lease_expired else DisplayStatus.STARTING
    if state == ServerState.STOPPING:
        return DisplayStatus.STOPPING
    if state == ServerState.FAILED:
        return DisplayStatus.FAILED
    if not process_alive:
        return DisplayStatus.STALE
    if not health_ok:
        return DisplayStatus.UNHEALTHY
    return DisplayStatus.RUNNING


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
        return entry is not None and process.is_alive(entry.pid)

    def touch(self, key: str) -> None:
        """Update last_used_at timestamp for a server. Call after session creation."""
        entry = registry.read(key)
        if entry is not None and entry.state == ServerState.RUNNING:
            entry.last_used_at = registry.now_iso()
            registry.write(entry)

    async def _status_for_entry(
        self, entry: RegistryEntry, *, health_timeout: float = 3.0
    ) -> ServerStatus:
        """Derive a ServerStatus for an already-fetched registry entry."""
        process_alive = process.is_alive(entry.pid) if entry.state == ServerState.RUNNING else False
        health_ok = False
        if process_alive:
            client = OpenCodeClient(
                base_url=f"http://127.0.0.1:{entry.port}",
                password=entry.password,
            )
            health_ok = await _is_health_ok(client, timeout=health_timeout)
        display = _compute_display_status(entry.state, process_alive, health_ok)
        return ServerStatus(
            entry=entry, process_alive=process_alive, health_ok=health_ok, display=display
        )

    async def status(self, key: str, *, health_timeout: float = 3.0) -> ServerStatus | None:
        """Return the computed status for key, or None if not in the registry."""
        entry = registry.read(key)
        if entry is None:
            return None
        return await self._status_for_entry(entry, health_timeout=health_timeout)

    # NOTE: list() and list_statuses() must stay defined in this order — once
    # `list` is bound as a class attribute (the method below), any later
    # annotation in this class body that writes the bare name `list[...]`
    # resolves to that method, not the builtin (see the Materials alias note
    # up top for the same issue). list_statuses()'s `-> list[ServerStatus]`
    # return annotation is defined above list() to avoid it.
    async def list_statuses(self, *, health_timeout: float = 1.0) -> list[ServerStatus]:
        """Return computed status for every ready registry entry."""
        return [
            await self._status_for_entry(entry, health_timeout=health_timeout)
            for entry, _ in self.list()
        ]

    def list(self) -> list[tuple[RegistryEntry, bool]]:
        """Return all ready registry entries with their liveness status."""
        return [
            (entry, process.is_alive(entry.pid))
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

        Returns True if the process was alive and was confirmed killed,
        False if it was already dead, not found, or couldn't be confirmed
        dead (in which case the entry is left in place, still discoverable,
        rather than forgotten while the process may still be running).
        """
        entry = registry.read(key)
        if entry is None:
            return False

        async def forget() -> None:
            await asyncio.to_thread(registry.delete_if_instance, key, entry.instance_id)

        if not process.is_alive(entry.pid):
            await forget()
            return False

        assert entry.pid is not None
        if not process.kill_group(entry.pid, signal.SIGTERM):
            await forget()
            return False

        if await process.wait_until_dead(entry.pid, entry.pid_start_time, timeout=5.0):
            await forget()
            return True

        process.kill_group(entry.pid, signal.SIGKILL)
        if await process.wait_until_dead(entry.pid, entry.pid_start_time, timeout=2.0):
            await forget()
            return True

        return False

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
            if process.is_alive(entry.pid):
                return self._attach(entry)
            # Stale — the process died after finishing startup. Scoped to
            # this generation so a concurrent fresh claim isn't clobbered.
            registry.delete_if_instance(key, entry.instance_id)

        # port/password/instance_id are generated here (not inside _start)
        # because a claim needs them all up front — pid is the only field
        # that isn't known until the subprocess actually exists.
        port = _find_free_port()
        password = secrets.token_urlsafe(32)
        instance_id = uuid.uuid4().hex
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
                instance_id=instance_id,
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
                instance_id=instance_id,
                started_at=timestamp,
                workspace=workspace,
                user_id=user_id,
            )
        except Exception:
            registry.delete_if_instance(key, instance_id)
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
            if entry.state == ServerState.RUNNING and process.is_alive(entry.pid):
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
        instance_id: str,
        started_at: str,
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

        proc = await process.spawn(
            "opencode",
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
            cwd=str(project_dir),
            env=process_env,
            output=output,
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
            await _wait_healthy(poll_client, process=proc)
        except Exception:
            await process.terminate(proc)
            raise

        registry.write(
            RegistryEntry(
                key=key,
                state=ServerState.RUNNING,
                pid=proc.pid,
                port=port,
                password=password,
                project_dir=str(project_dir),
                server_dir=str(server_dir) if server_dir else None,
                started_at=started_at,
                claimed_at=started_at,
                last_used_at=started_at,
                runtime_version=version("opencode-runtime"),
                workspace=workspace,
                user_id=user_id,
                instance_id=instance_id,
                pid_start_time=process.start_time(proc.pid),
            )
        )

        return _ManagedServer(
            key=key,
            process=proc,
            client=client,
            server_dir=server_dir,
        )
