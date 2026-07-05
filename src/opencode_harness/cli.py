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
from pathlib import Path

from .registry import (
    RegistryEntry,
    delete,
    is_alive,
    list_all,
    now_iso,
    read,
    write,
)


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


async def _serve(args: argparse.Namespace) -> None:
    from .client import OpenCodeClient
    from .server import _compute_runtime_key, _find_free_port, _prepare_dir, _terminate_process

    if shutil.which("opencode") is None:
        sys.exit(
            "error: opencode binary not found on PATH\n  Install with: npm install -g opencode-ai"
        )

    project_dir = Path(args.project_dir).resolve()
    runtime_dir = Path(args.runtime_dir).resolve() if args.runtime_dir else None
    materials = args.materials or None

    key = _compute_runtime_key(
        workspace=None, user_id=None, project_dir=project_dir, materials=materials, config={}
    )

    existing = read(key)
    if existing is not None:
        if is_alive(existing.pid):
            sys.exit(
                f"error: server already running  key={key}  pid={existing.pid}  port={existing.port}\n"
                f"  use 'opencode-harness stop {key}' to stop it first"
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

    print(f"starting  port={port}  pid={process.pid}", flush=True)
    for elapsed in range(60):
        if process.returncode is not None:
            sys.exit(f"error: opencode process exited with code {process.returncode}")
        try:
            await client.health()
            break
        except Exception:
            await asyncio.sleep(1.0)
            print(f"\rwaiting... {elapsed + 1}s", end="", flush=True)
    else:
        await _terminate_process(process)
        sys.exit("error: server did not become healthy within 60s")

    write(
        RegistryEntry(
            key=key,
            pid=process.pid,
            port=port,
            password=password,
            project_dir=str(project_dir),
            server_dir=str(server_dir) if server_dir else None,
            started_at=now_iso(),
        )
    )
    print(f"\rstarted  key={key}  port={port}  pid={process.pid}")


def cmd_serve(args: argparse.Namespace) -> None:
    try:
        asyncio.run(_serve(args))
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# ps
# ---------------------------------------------------------------------------


def cmd_ps(_args: argparse.Namespace) -> None:
    fmt = "{:<16}  {:>6}  {:>6}  {:<8}  {}"
    print(fmt.format("KEY", "PID", "PORT", "STATUS", "PROJECT_DIR"))
    print("-" * 72)
    for e in list_all():
        print(
            fmt.format(e.key, e.pid, e.port, "alive" if is_alive(e.pid) else "dead", e.project_dir)
        )


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def cmd_stop(args: argparse.Namespace) -> None:
    entry = read(args.key)
    if entry is None:
        sys.exit(f"error: key {args.key!r} not found in registry")

    if is_alive(entry.pid):
        os.kill(entry.pid, signal.SIGTERM)
        print(f"stopped  key={entry.key}  pid={entry.pid}")
    else:
        print(f"warning: process {entry.pid} was already dead")
    delete(entry.key)


# ---------------------------------------------------------------------------
# stop-all
# ---------------------------------------------------------------------------


def cmd_stop_all(_args: argparse.Namespace) -> None:
    entries = list_all()
    if not entries:
        print("no servers in registry")
        return

    for entry in entries:
        if is_alive(entry.pid):
            os.kill(entry.pid, signal.SIGTERM)
            print(f"stopped  key={entry.key}  pid={entry.pid}")
        else:
            print(f"skipped  key={entry.key}  (already dead)")
        delete(entry.key)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def cmd_health(args: argparse.Namespace) -> None:
    import httpx

    from .client import OpenCodeClient

    entry = read(args.key)
    if entry is None:
        sys.exit(f"error: key {args.key!r} not found in registry")

    client = OpenCodeClient(base_url=f"http://127.0.0.1:{entry.port}", password=entry.password)
    try:
        result = asyncio.run(client.health())
        print(f"healthy={result.get('healthy')}  version={result.get('version')}")
    except httpx.HTTPError as exc:
        sys.exit(f"error: {exc}")


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
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("ps", help="list tracked servers")
    p.set_defaults(func=cmd_ps)

    p = sub.add_parser("stop", help="stop a server by key")
    p.add_argument("key", help="server key (from ps)")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("stop-all", help="stop all tracked servers")
    p.set_defaults(func=cmd_stop_all)

    p = sub.add_parser("health", help="check health of a server by key")
    p.add_argument("key", help="server key (from ps)")
    p.set_defaults(func=cmd_health)

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()
    args.func(args)
