# Changelog

## [Unreleased]

## [0.2.0] - 2026-07-06

### Added
- `opencode-harness` CLI — `serve`, `ps`, `stop`, `stop-all`, `health` subcommands
- `serve` starts an opencode server detached (background process)
- `serve --workspace` and `--user-id` for multi-tenant isolation — each unique combination gets its own server
- `ps` lists all tracked servers with status, uptime, and project; shows workspace/user columns when in use
- `health` checks liveness of a server by ID
- `stop` / `stop-all` terminate servers and clean up registry entries
- `registry` module — PID file registry at `~/.opencode-harness/servers/` with `0o600` permissions
- Polished terminal UI — ANSI colour, structured output, uptime display

## [0.1.0] - 2026-07-05

### Added
- `OpenCodeHarness` — managed server lifecycle with async context manager
- `OpenCodeSession` — `ask()`, `stream()`, `abort()`, `close()`
- `ServerManager` — per-`(workspace, user_id, project_dir, materials, config)` server registry with key-based isolation
- `OpenCodeClient` — thin HTTP/SSE client with HTTP Basic auth
- `stream()` yields every event OpenCode emits unfiltered — text deltas, tool calls, thinking, status updates, and permission requests; `event.text` populated for text-bearing events, `event.raw` carries the full payload
- `OpenCodeEvent` and `OpenCodeResponse` — typed output primitives
- `OpenCodeHarnessError`, `OpenCodeNotFoundError`, `OpenCodeServerError`, `OpenCodeTimeoutError` — error hierarchy
- `runtime_dir` — opt-in isolation giving each server its own `HOME`, config, and conversation history
- `materials` — copy OpenCode-native files (`AGENTS.md`, `.opencode/skills/`, etc.) into the server before start
- `workspace` and `user_id` — multi-tenant session isolation
- `raw_client` — escape hatch to any OpenCode server endpoint
- `session_id` — pass an existing OpenCode session ID to resume a previous conversation; readable after the first `ask()`/`stream()` call
