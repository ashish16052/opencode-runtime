"""
Internal server lifecycle helpers.

All symbols in this module are private to opencode-harness.
Nothing here is exported in __all__.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import OpenCodeClient


def _find_free_port(host: str = "127.0.0.1") -> int:
    """Bind to port 0 and let the OS pick a free ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


async def _wait_healthy(client: OpenCodeClient, timeout: float = 20.0) -> None:
    """Poll GET /global/health until the server responds or timeout expires."""
    from .exceptions import OpenCodeTimeoutError

    deadline = asyncio.get_event_loop().time() + timeout
    last_exc: Exception | None = None

    while asyncio.get_event_loop().time() < deadline:
        try:
            await client.health()
            return
        except Exception as exc:
            last_exc = exc
            await asyncio.sleep(0.25)

    raise OpenCodeTimeoutError(
        f"opencode server did not become healthy within {timeout}s (last error: {last_exc})"
    )


def _prepare_dir(
    server_dir: Path,
    config: dict[str, Any],
    materials: str | Path | list[str | Path] | None,
) -> None:
    """Write opencode.json and overlay materials into server_dir."""
    from .exceptions import OpenCodeHarnessError

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
                raise OpenCodeHarnessError(f"materials path does not exist: {src}")
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
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        process.kill()
