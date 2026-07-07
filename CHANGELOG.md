# Changelog

## [Unreleased]

## [0.3.0] - 2026-07-07

### Added
- CLI ↔ library registry integration — instances started by `OpenCodeRuntime` appear in `opencode-runtime ps` and can be managed with `stop` and `health`
- `OpenCodeRuntime` and `opencode-runtime serve` share the same registry — same key, same instance, no duplicate spawning
- Registry as single source of truth — `ServerManager` always consults the registry on every `session()` call; external changes (CLI `stop-all`, another process) are reflected immediately without restart
- `stop()` and `stop-all` always terminate the instance process regardless of which actor started it; registry entry is cleaned up atomically
- `workspace` and `user_id` stored in registry entries for both library and CLI-started instances

## [0.2.0] - 2026-07-06

### Added
- `opencode-runtime` CLI — `serve`, `ps`, `stop`, `stop-all`, `health` subcommands
- `serve` starts an OpenCode instance detached (background process)
- `serve --workspace` and `--user-id` for multi-tenant isolation — each unique combination gets its own instance
- `ps` lists all tracked instances with status, uptime, and project; shows workspace/user columns when in use
- `health` checks liveness of an instance by ID
- `stop` / `stop-all` terminate instances and clean up registry entries
- `registry` module — PID file registry at `~/.opencode-runtime/servers/` with `0o600` permissions
- Polished terminal UI — ANSI colour, structured output, uptime display

## [0.1.0] - 2026-07-05

### Added
- `OpenCodeRuntime` — managed instance lifecycle with async context manager
- `OpenCodeSession` — `ask()`, `stream()`, `abort()`, `close()`
- `ServerManager` — per-`(workspace, user_id, project_dir, materials, config)` registry with key-based workspace isolation
- `OpenCodeClient` — thin HTTP/SSE client with HTTP Basic auth
- `stream()` yields every event OpenCode emits unfiltered — text deltas, tool calls, thinking, status updates, and permission requests; `event.text` populated for text-bearing events, `event.raw` carries the full payload
- `OpenCodeEvent` and `OpenCodeResponse` — typed output primitives
- `OpenCodeRuntimeError`, `OpenCodeNotFoundError`, `OpenCodeServerError`, `OpenCodeTimeoutError` — error hierarchy
- `runtime_dir` — opt-in isolation giving each instance its own `HOME`, config, and conversation history
- `materials` — copy OpenCode-native files (`AGENTS.md`, `.opencode/skills/`, etc.) into the workspace before start
- `workspace` and `user_id` — multi-tenant session isolation
- `raw_client` — escape hatch to any OpenCode server endpoint
- `session_id` — pass an existing OpenCode session ID to resume a previous conversation; readable after the first `ask()`/`stream()` call
