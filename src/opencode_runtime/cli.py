"""
CLI entry point for opencode-runtime instance management.

Commands:
    serve       Start an OpenCode instance (detached)
    ps          List all instances tracked in the registry
    stop        Stop an instance by key
    stop-all    Stop all tracked instances
    health      Check health of an instance by key
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from .server import DisplayStatus, ServerManager, _compute_runtime_key

# ---------------------------------------------------------------------------
# ANSI
# ---------------------------------------------------------------------------

_R = "\033[0m"
_DIM = "\033[2m"


def _green(s: str) -> str:
    return f"\033[32m{s}{_R}"


def _yellow(s: str) -> str:
    return f"\033[33m{s}{_R}"


def _red(s: str) -> str:
    return f"\033[31m{s}{_R}"


def _cyan(s: str) -> str:
    return f"\033[36m{s}{_R}"


def _dim(s: str) -> str:
    return f"{_DIM}{s}{_R}"


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


_STATUS_ICONS: dict[DisplayStatus, str] = {
    DisplayStatus.RUNNING: "●",
    DisplayStatus.STARTING: "◐",
    DisplayStatus.UNHEALTHY: "▲",
    DisplayStatus.STALE: "○",
    DisplayStatus.FAILED: "✗",
}
_STATUS_COLORS = {
    DisplayStatus.RUNNING: _green,
    DisplayStatus.STARTING: _yellow,
    DisplayStatus.UNHEALTHY: _red,
    DisplayStatus.STALE: _dim,
    DisplayStatus.FAILED: _red,
}


def _status_display(status: DisplayStatus) -> str:
    """Render a ServerStatus.display value as a coloured icon + label."""
    icon = _STATUS_ICONS.get(status, "?")
    color = _STATUS_COLORS.get(status, _dim)
    return color(f"{icon} {status.value}")


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


async def _serve(args: argparse.Namespace) -> None:
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

    manager = ServerManager()

    existing = manager.find(key)
    if existing is not None:
        if manager.is_alive(key):
            sys.exit(
                _yellow(f"● Server already running  id={existing.key}  pid={existing.pid}\n")
                + _dim(f"  use: opencode-runtime stop {existing.key}")
            )

    server_dir: Path | None = None
    if runtime_dir is not None:
        server_dir = runtime_dir / "servers" / key

    print(_yellow("● Starting opencode server..."), flush=True)

    server = await manager.get_or_start(
        key=key,
        project_dir=project_dir,
        server_dir=server_dir,
        materials=materials,
        config={},
        env={},
        workspace=args.workspace,
        user_id=args.user_id,
    )

    entry = manager.find(key)
    assert entry is not None

    print(f"\r{_green('✓ Server started')}\n")
    _row("ID", key)
    if args.workspace:
        _row("Workspace", args.workspace)
    if args.user_id:
        _row("User", args.user_id)
    _row("Status", _green("● alive"))
    _row("URL", server.client.base_url)
    _row("PID", _dim(str(entry.pid)))
    _row("Project", _dim(_home(str(project_dir))))
    print()
    print(_dim(f"  opencode-runtime health {key}"))
    print(_dim(f"  opencode-runtime stop   {key}"))


def cmd_serve(args: argparse.Namespace) -> None:
    try:
        asyncio.run(_serve(args))
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# ps
# ---------------------------------------------------------------------------


def cmd_ps(_args: argparse.Namespace) -> None:
    statuses = asyncio.run(ServerManager().list_statuses())
    show_workspace = any(st.entry.workspace for st in statuses)
    show_user = any(st.entry.user_id for st in statuses)

    cols = ["  {:<18}", "{:>6}", "{:>6}", "{:<11}", "{:>8}"]
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

    for st in statuses:
        e = st.entry
        status_plain = f"{_STATUS_ICONS.get(st.display, '?')} {st.display.value}"
        status_coloured = _status_display(st.display)

        # Compute uptime
        try:
            started = datetime.fromisoformat(e.started_at)
            uptime_secs = int((datetime.now(timezone.utc) - started).total_seconds())
            if uptime_secs < 60:
                uptime_str = f"{uptime_secs}s"
            elif uptime_secs < 3600:
                uptime_str = f"{uptime_secs // 60}m"
            else:
                uptime_str = f"{uptime_secs // 3600}h"
        except Exception:
            uptime_str = "?"

        vals = [e.key, str(e.pid), str(e.port), status_plain, uptime_str]
        if show_workspace:
            vals.append(e.workspace or "-")
        if show_user:
            vals.append(e.user_id or "-")
        vals.append(_home(e.project_dir))
        row = fmt.format(*vals)
        dimmed_row = _dim(row).replace(status_plain, f"{status_coloured}{_DIM}", 1)
        print(dimmed_row)


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def cmd_stop(args: argparse.Namespace) -> None:
    from . import registry

    manager = ServerManager()
    entry = registry.read(args.key)
    if entry is None:
        sys.exit(_red(f"✗ ID {args.key!r} not found in registry"))

    was_alive = asyncio.run(manager.stop(args.key))
    if not was_alive:
        print(_yellow(f"  ● process {entry.pid} was already dead"))

    print(f"{_green('✓ Server stopped')}\n")
    _row("ID", entry.key)
    _row("PID", _dim(str(entry.pid)) if entry.pid else _dim("(none)"))


# ---------------------------------------------------------------------------
# stop-all
# ---------------------------------------------------------------------------


def cmd_stop_all(_args: argparse.Namespace) -> None:
    manager = ServerManager()
    entries = manager.list()
    if not entries:
        print(_dim("  no servers running"))
        return

    asyncio.run(manager.stop_all())

    print(f"{_green(f'✓ Stopped {len(entries)} server(s)')}\n")
    for e, _ in entries:
        print(f"  {_dim(e.key)}   {_dim(f'pid {e.pid}')}")


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def cmd_health(args: argparse.Namespace) -> None:
    manager = ServerManager()
    st = asyncio.run(manager.status(args.key))
    if st is None:
        sys.exit(_red(f"✗ ID {args.key!r} not found in registry"))
    entry = st.entry

    if st.display == DisplayStatus.RUNNING:
        try:
            result = asyncio.run(manager.health(args.key))
            version = result.get("version")
            print(
                _green("✓ healthy")
                + f"   {_dim(f'version {version}')}"
                + f"   {_dim(f'http://127.0.0.1:{entry.port}')}"
            )
        except Exception as exc:
            sys.exit(_red(f"✗ unhealthy: /global/health failed: {exc}"))
    elif st.display == DisplayStatus.STARTING:
        try:
            claimed = datetime.fromisoformat(entry.claimed_at)
            age_secs = int((datetime.now(timezone.utc) - claimed).total_seconds())
            sys.exit(_yellow(f"◐ starting: claimed {age_secs}s ago, health check pending"))
        except Exception:
            sys.exit(_yellow("◐ starting: awaiting health check"))
    elif st.display == DisplayStatus.UNHEALTHY:
        sys.exit(
            _red(
                f"✗ unhealthy: process running (pid {entry.pid}) but /global/health endpoint failed"
            )
        )
    elif st.display == DisplayStatus.STALE:
        sys.exit(_red(f"✗ stale: registry entry exists but pid {entry.pid} is not running"))
    elif st.display == DisplayStatus.FAILED:
        sys.exit(_red("✗ failed: startup failed or lease expired"))
    else:
        sys.exit(_red(f"✗ unknown: {st.display}"))


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def cmd_inspect(args: argparse.Namespace) -> None:
    manager = ServerManager()
    st = asyncio.run(manager.status(args.key))
    if st is None:
        sys.exit(_red(f"✗ ID {args.key!r} not found in registry"))
    entry = st.entry

    # Compute uptime
    try:
        started = datetime.fromisoformat(entry.started_at)
        uptime_secs = int((datetime.now(timezone.utc) - started).total_seconds())
        if uptime_secs < 60:
            uptime = f"{uptime_secs}s"
        elif uptime_secs < 3600:
            uptime = f"{uptime_secs // 60}m {uptime_secs % 60}s"
        else:
            uptime = f"{uptime_secs // 3600}h {(uptime_secs % 3600) // 60}m"
    except Exception:
        uptime = "?"

    # Compute idle time (time since last use)
    if entry.last_used_at:
        try:
            last_used = datetime.fromisoformat(entry.last_used_at)
            idle_secs = int((datetime.now(timezone.utc) - last_used).total_seconds())
            if idle_secs < 60:
                idle = f"{idle_secs}s ago"
            elif idle_secs < 3600:
                idle = f"{idle_secs // 60}m ago"
            else:
                idle = f"{idle_secs // 3600}h ago"
        except Exception:
            idle = "?"
    else:
        idle = "-"

    print()
    _row("ID", entry.key)
    _row("Status", _status_display(st.display))
    _row("Project", _home(entry.project_dir))
    if entry.workspace:
        _row("Workspace", entry.workspace)
    if entry.user_id:
        _row("User", entry.user_id)
    _row("PID", _dim(str(entry.pid)) if entry.pid else _dim("(none)"))
    _row("Port", _dim(str(entry.port)))
    _row("Uptime", uptime)
    _row("Last used", idle)
    if entry.runtime_version:
        _row("Runtime", entry.runtime_version)
    if entry.server_dir:
        log_file = _home(str(Path(entry.server_dir) / "opencode.log"))
        _row("Log file", _dim(log_file))
    print()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="opencode-runtime", description="Manage OpenCode instance processes."
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

    p = sub.add_parser("inspect", help="show detailed server information")
    p.add_argument("key", help="server id (from ps)")
    p.set_defaults(func=cmd_inspect)

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()
    args.func(args)
