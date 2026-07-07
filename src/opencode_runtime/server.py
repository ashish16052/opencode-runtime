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
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import OpenCodeClient

from .registry import RegistryEntry


@dataclass
class _ManagedServer:
    """A running opencode server process tracked by the runtime."""

    key: str
    process: asyncio.subprocess.Process | None  # set during _start(); None from get_or_start()
    client: OpenCodeClient
    server_dir: Path | None  # None when runtime_dir is not set (no isolation)


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
    from .exceptions import OpenCodeTimeoutError

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
    materials: str | Path | list[str | Path] | None,
) -> None:
    """Write opencode.json and overlay materials into server_dir."""
    from .exceptions import OpenCodeRuntimeError

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
    materials: str | Path | list[str | Path] | None,
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

    The registry is the single source of truth. There is no in-memory cache —
    every call consults the registry so that external actors (CLI stop-all,
    another process) are always reflected immediately.
    """

    def find(self, key: str) -> RegistryEntry | None:
        """Return the registry entry for key, or None if not found."""
        from .registry import read as registry_read

        return registry_read(key)

    def is_alive(self, key: str) -> bool:
        """Return True if the server for key is running."""
        from .registry import is_alive
        from .registry import read as registry_read

        entry = registry_read(key)
        return entry is not None and is_alive(entry.pid)

    def list(self) -> list[tuple[RegistryEntry, bool]]:
        """Return all registry entries with their liveness status."""
        from .registry import is_alive
        from .registry import list_all

        return [(entry, is_alive(entry.pid)) for entry in list_all()]

    async def health(self, key: str) -> dict[str, Any]:
        """Return health info for the server at key.

        Raises ``OpenCodeServerError`` if key is not in the registry or the
        server is unreachable.
        """
        import httpx

        from .client import OpenCodeClient
        from .exceptions import OpenCodeServerError
        from .registry import read as registry_read

        entry = registry_read(key)
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
        from .registry import delete as registry_delete
        from .registry import is_alive
        from .registry import read as registry_read

        entry = registry_read(key)
        if entry is None:
            return False

        registry_delete(key)

        if not is_alive(entry.pid):
            return False

        import signal

        try:
            os.kill(entry.pid, signal.SIGTERM)
        except ProcessLookupError:
            return False
        # Wait for the process to exit (up to 5s, then SIGKILL).
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            if not is_alive(entry.pid):
                return True
            await asyncio.sleep(0.1)
        try:
            os.kill(entry.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return True

    async def stop_all(self) -> None:
        """Kill all servers tracked in the registry."""
        from .registry import list_all

        for entry in list_all():
            await self.stop(entry.key)

    async def get_or_start(
        self,
        *,
        key: str,
        project_dir: Path,
        server_dir: Path | None,
        materials: str | Path | list[str | Path] | None,
        config: dict[str, Any],
        env: dict[str, str],
        workspace: str | None = None,
        user_id: str | None = None,
    ) -> _ManagedServer:
        """Return a client for the running server, starting one if needed."""
        from .client import OpenCodeClient
        from .registry import delete as registry_delete
        from .registry import is_alive
        from .registry import read as registry_read

        entry = registry_read(key)
        if entry is not None and is_alive(entry.pid):
            return _ManagedServer(
                key=key,
                process=None,
                client=OpenCodeClient(
                    base_url=f"http://127.0.0.1:{entry.port}",
                    password=entry.password,
                ),
                server_dir=Path(entry.server_dir) if entry.server_dir else None,
            )

        # Not running — clean up stale registry entry and start fresh.
        if entry is not None:
            registry_delete(key)

        return await self._start(
            key=key,
            project_dir=project_dir,
            server_dir=server_dir,
            materials=materials,
            config=config,
            env=env,
            workspace=workspace,
            user_id=user_id,
        )

    async def _start(
        self,
        *,
        key: str,
        project_dir: Path,
        server_dir: Path | None,
        materials: str | Path | list[str | Path] | None,
        config: dict[str, Any],
        env: dict[str, str],
        workspace: str | None = None,
        user_id: str | None = None,
    ) -> _ManagedServer:
        """Start a new OpenCode instance and return a _ManagedServer."""
        from .client import OpenCodeClient
        from .exceptions import OpenCodeNotFoundError

        if shutil.which("opencode") is None:
            raise OpenCodeNotFoundError(
                "opencode binary not found on PATH. Install it with: npm install -g opencode-ai"
            )

        if server_dir is not None:
            server_dir.mkdir(parents=True, exist_ok=True)
            (server_dir / "tmp").mkdir(exist_ok=True)
            _prepare_dir(server_dir, config, materials)

        port = _find_free_port()
        password = secrets.token_urlsafe(32)

        process_env = {**os.environ, **env}
        process_env["OPENCODE_SERVER_PASSWORD"] = password

        if server_dir is not None:
            process_env["HOME"] = str(server_dir)
            process_env["TMPDIR"] = str(server_dir / "tmp")
            process_env["OPENCODE_CONFIG_HOME"] = str(server_dir)

        if server_dir is not None:
            log_file = open(server_dir / "opencode.log", "ab")
            stdout = log_file
            stderr = log_file
        else:
            stdout = asyncio.subprocess.DEVNULL
            stderr = asyncio.subprocess.DEVNULL

        process = await asyncio.create_subprocess_exec(
            "opencode",
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
            cwd=str(project_dir),
            env=process_env,
            stdout=stdout,
            stderr=stderr,
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

        from .registry import RegistryEntry, now_iso
        from .registry import write as registry_write

        registry_write(
            RegistryEntry(
                key=key,
                pid=process.pid,
                port=port,
                password=password,
                project_dir=str(project_dir),
                server_dir=str(server_dir) if server_dir else None,
                started_at=now_iso(),
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
