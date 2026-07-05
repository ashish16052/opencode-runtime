"""
CLI entry point for opencode-harness server management.

Commands:
    serve       Start an opencode server (detached)
    ps          List all servers tracked in the registry
    stop        Stop a server by key
    stop-all    Stop all tracked servers
    health      Check health of a server by key
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import shutil
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from .registry import RegistryEntry, delete, is_alive, list_all, now_iso, read, write

# ---------------------------------------------------------------------------
# ANSI
# ---------------------------------------------------------------------------

_R = "\033[0m"


def _green(s: str) -> str:
    return f"\033[32m{s}{_R}"


def _yellow(s: str) -> str:
    return f"\033[33m{s}{_R}"


def _red(s: str) -> str:
    return f"\033[31m{s}{_R}"


def _cyan(s: str) -> str:
    return f"\033[36m{s}{_R}"


def _dim(s: str) -> str:
    return f"\033[2m{s}{_R}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _home(path: str) -> str:
    try:
        return "~/" + str(Path(path).relative_to(Path.home()))
    except ValueError:
        return path


def _uptime(started_at: str, alive: bool) -> str:
    try:
        mins = max(
            0,
            int(
                (datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds()
                // 60
            ),
        )
    except Exception:
        return "?"
    return f"Up {mins}m" if alive else f"Dead {mins}m"


def _row(label: str, value: str) -> None:
    print(f"  {_cyan(f'{label:<9}')}  {value}")


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


async def _serve(args: argparse.Namespace) -> None:
    from .client import OpenCodeClient
    from .server import _compute_runtime_key, _find_free_port, _prepare_dir, _terminate_process

    if shutil.which("opencode") is None:
        sys.exit(
            _red("✗ opencode binary not found on PATH\n  Install with: npm install -g opencode-ai")
        )

    project_dir = Path(args.project_dir).resolve()
    runtime_dir = Path(args.runtime_dir).resolve() if args.runtime_dir else None
    materials = args.materials or None

    key = _compute_runtime_key(
        workspace=args.workspace,
        user_id=args.user_id,
        project_dir=project_dir,
        materials=materials,
        config={},
    )

    existing = read(key)
    if existing is not None:
        if is_alive(existing.pid):
            sys.exit(
                _yellow(f"● Server already running  id={existing.key}  pid={existing.pid}\n")
                + _dim(f"  use: opencode-harness stop {existing.key}")
            )
        delete(key)

    server_dir: Path | None = None
    if runtime_dir is not None:
        server_dir = runtime_dir / "servers" / key
        server_dir.mkdir(parents=True, exist_ok=True)
        (server_dir / "tmp").mkdir(exist_ok=True)
        _prepare_dir(server_dir, {}, materials)

    port = _find_free_port()
    password = secrets.token_urlsafe(32)

    env = {**os.environ, "OPENCODE_SERVER_PASSWORD": password}
    if server_dir is not None:
        env.update(
            HOME=str(server_dir),
            TMPDIR=str(server_dir / "tmp"),
            OPENCODE_CONFIG_HOME=str(server_dir),
        )

    log = open(server_dir / "opencode.log", "ab") if server_dir else asyncio.subprocess.DEVNULL
    process = await asyncio.create_subprocess_exec(
        "opencode",
        "serve",
        "--hostname",
        "127.0.0.1",
        "--port",
        str(port),
        cwd=str(project_dir),
        env=env,
        stdout=log,
        stderr=log,
    )

    client = OpenCodeClient(base_url=f"http://127.0.0.1:{port}", password=password)

    print(_yellow("● Starting opencode server..."), flush=True)
    for elapsed in range(60):
        if process.returncode is not None:
            sys.exit(_red(f"✗ opencode process exited with code {process.returncode}"))
        try:
            await client.health()
            break
        except Exception:
            await asyncio.sleep(1.0)
            print(f"\r  {_dim(f'waiting... {elapsed + 1}s')}", end="", flush=True)
    else:
        await _terminate_process(process)
        sys.exit(_red("✗ Server did not become healthy within 60s"))

    write(
        RegistryEntry(
            key=key,
            pid=process.pid,
            port=port,
            password=password,
            project_dir=str(project_dir),
            server_dir=str(server_dir) if server_dir else None,
            started_at=now_iso(),
            workspace=args.workspace,
            user_id=args.user_id,
        )
    )

    print(f"\r{_green('✓ Server started')}\n")
    _row("ID", key)
    if args.workspace:
        _row("Workspace", args.workspace)
    if args.user_id:
        _row("User", args.user_id)
    _row("Status", _green("● alive"))
    _row("URL", f"http://127.0.0.1:{port}")
    _row("PID", _dim(str(process.pid)))
    _row("Project", _dim(_home(str(project_dir))))
    print()
    print(_dim(f"  opencode-harness health {key}"))
    print(_dim(f"  opencode-harness stop   {key}"))


def cmd_serve(args: argparse.Namespace) -> None:
    try:
        asyncio.run(_serve(args))
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# ps
# ---------------------------------------------------------------------------


def cmd_ps(_args: argparse.Namespace) -> None:
    entries = list_all()
    show_workspace = any(e.workspace for e in entries)
    show_user = any(e.user_id for e in entries)

    # Build format string dynamically
    cols = ["  {:<18}", "{:>6}", "{:>6}", "{:<7}", "{:>8}"]
    headers = ["ID", "PID", "PORT", "STATUS", "UPTIME"]
    if show_workspace:
        cols.append("{:<12}")
        headers.append("WORKSPACE")
    if show_user:
        cols.append("{:<12}")
        headers.append("USER")
    cols.append("{}")
    headers.append("PROJECT")
    fmt = "  ".join(cols)

    print(_cyan(fmt.format(*headers)))
    print(_dim("  " + "─" * (70 + 14 * show_workspace + 14 * show_user)))

    for e in entries:
        alive = is_alive(e.pid)
        status_plain = "● alive" if alive else "● dead"
        status_coloured = _green(status_plain) if alive else _red(status_plain)
        vals = [e.key, str(e.pid), str(e.port), status_plain, _uptime(e.started_at, alive)]
        if show_workspace:
            vals.append(e.workspace or "-")
        if show_user:
            vals.append(e.user_id or "-")
        vals.append(_home(e.project_dir))
        row = fmt.format(*vals)
        row = row.replace(status_plain, status_coloured, 1)
        print(_dim(row).replace(_dim(status_coloured), status_coloured, 1))


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def cmd_stop(args: argparse.Namespace) -> None:
    entry = read(args.key)
    if entry is None:
        sys.exit(_red(f"✗ ID {args.key!r} not found in registry"))

    if is_alive(entry.pid):
        os.kill(entry.pid, signal.SIGTERM)
    else:
        print(_yellow(f"  ● process {entry.pid} was already dead"))
    delete(entry.key)

    print(f"{_green('✓ Server stopped')}\n")
    _row("ID", entry.key)
    _row("PID", _dim(str(entry.pid)))


# ---------------------------------------------------------------------------
# stop-all
# ---------------------------------------------------------------------------


def cmd_stop_all(_args: argparse.Namespace) -> None:
    entries = list_all()
    if not entries:
        print(_dim("  no servers running"))
        return

    for entry in entries:
        if is_alive(entry.pid):
            os.kill(entry.pid, signal.SIGTERM)
        delete(entry.key)

    print(f"{_green(f'✓ Stopped {len(entries)} server(s)')}\n")
    for e in entries:
        print(f"  {_dim(e.key)}   {_dim(f'pid {e.pid}')}")


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def cmd_health(args: argparse.Namespace) -> None:
    import httpx

    from .client import OpenCodeClient

    entry = read(args.key)
    if entry is None:
        sys.exit(_red(f"✗ ID {args.key!r} not found in registry"))

    url = f"http://127.0.0.1:{entry.port}"
    client = OpenCodeClient(base_url=url, password=entry.password)
    try:
        result = asyncio.run(client.health())
        version = result.get("version")
        print(_green("✓ healthy") + f"   {_dim(f'version {version}')}" + f"   {_dim(url)}")
    except httpx.HTTPError as exc:
        sys.exit(_red(f"✗ unreachable   {url}\n  {exc}"))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="opencode-harness", description="Manage opencode server processes."
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    p = sub.add_parser("serve", help="start an opencode server (detached)")
    p.add_argument(
        "--project-dir", default=".", metavar="DIR", help="project directory (default: .)"
    )
    p.add_argument(
        "--runtime-dir", default=None, metavar="DIR", help="isolated runtime directory (optional)"
    )
    p.add_argument(
        "--materials",
        action="append",
        metavar="PATH",
        help="materials path(s) to overlay (repeatable)",
    )
    p.add_argument(
        "--workspace", default=None, metavar="NAME", help="tenant workspace identifier (optional)"
    )
    p.add_argument("--user-id", default=None, metavar="ID", help="user identifier (optional)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("ps", help="list tracked servers")
    p.set_defaults(func=cmd_ps)

    p = sub.add_parser("stop", help="stop a server by id")
    p.add_argument("key", help="server id (from ps)")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("stop-all", help="stop all tracked servers")
    p.set_defaults(func=cmd_stop_all)

    p = sub.add_parser("health", help="check health of a server by id")
    p.add_argument("key", help="server id (from ps)")
    p.set_defaults(func=cmd_health)

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()
    args.func(args)
