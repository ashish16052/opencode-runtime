"""
OS process primitives: spawning, identity, signaling, and liveness.

Kept separate from registry.py (which only persists JSON state) and
server.py (which orchestrates OpenCode-specific startup and health) so
that everything OS-process-specific lives in one place — and so a future
non-local backend (Docker, remote) could replace just this module.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from typing import Any


async def spawn(
    *args: str, cwd: str, env: dict[str, str], output: Any
) -> asyncio.subprocess.Process:
    """Start a subprocess as its own process group leader.

    start_new_session=True means kill_group() reaches children the process
    spawns too, not just the pid we hold.
    """
    return await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=env,
        stdout=output,
        stderr=output,
        start_new_session=True,
    )


def is_alive(pid: int | None) -> bool:
    """Return True if pid is set and a process with it is running."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def start_time(pid: int | None) -> str | None:
    """Return pid's process start time as reported by `ps`, or None if it's not running."""
    if pid is None:
        return None
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def is_same(pid: int | None, started_at: str | None) -> bool:
    """Return True if pid is alive and, when started_at is known, still matches it.

    started_at is None for entries written before this field existed —
    those fall back to a plain liveness check.
    """
    if not is_alive(pid):
        return False
    if started_at is None:
        return True
    return start_time(pid) == started_at


def kill_group(pid: int, sig: int) -> bool:
    """Send sig to pid's process group. Returns False if it's already gone."""
    try:
        os.killpg(os.getpgid(pid), sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


async def terminate(process: asyncio.subprocess.Process) -> None:
    """Terminate a process group gracefully, kill if it doesn't exit within 5s."""
    if process.returncode is not None:
        return  # already exited
    if not kill_group(process.pid, signal.SIGTERM):
        return  # already dead
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        kill_group(process.pid, signal.SIGKILL)


async def wait_until_dead(pid: int, started_at: str | None, timeout: float) -> bool:
    """Poll until pid is confirmed gone, or timeout elapses."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if not await asyncio.to_thread(is_same, pid, started_at):
            return True
        await asyncio.sleep(0.1)
    return not is_same(pid, started_at)
