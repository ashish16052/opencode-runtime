# Integrating opencode-runtime into corridor-platform

This document covers what changes in corridor-platform when adopting `opencode-runtime`,
what corridor keeps owning, and how the overall complexity picture changes.

---

## What the runtime owns after migration

These concerns move out of corridor-platform entirely and into the library:

| Concern | Currently in corridor | After migration |
|---|---|---|
| Spawn `opencode serve` subprocess | `utils.py` `_default_process_factory` | `ServerManager._start()` |
| Per-user isolated `HOME` / `TMPDIR` | `environment.py` lines 34–63 | `server.py:327–330` (automatic when `runtime_dir` is set) |
| Random port allocation | `utils.py` `_allocate_server_target` | `_find_free_port()` |
| Random password generation + auth | `utils.py` lines 430–438 | `secrets.token_urlsafe(32)` + Basic auth in client |
| Health-check polling until ready | `service.py` `_wait_for_server_ready` | `_wait_healthy()` |
| Server reuse for the same user | `server_registry.py` `_acquire` | `ServerManager.get_or_start()` |
| Stale PID cleanup | `server_registry.py` `_pid_is_alive` | `registry.is_alive()` + auto-clean in `get_or_start` |
| `POST /session` creation | `service.py` | `OpenCodeSession.ask()` / `stream()` lazy init |
| `POST /session/{id}/prompt_async` | `utils.py` `HttpxOpenCodeClient` | `OpenCodeClient.send()` |
| SSE bus consumption + filtering | `utils.py` `iter_sse()` | `OpenCodeClient.events()` |
| Terminate SIGTERM → SIGKILL (5s) | `utils.py` `_terminate_process` | `ServerManager.stop()` |
| CLI `ps` / `stop` / `health` | none today | `opencode-runtime` CLI |

---

## What corridor keeps owning

These are corridor-specific. They do not belong in a generic library and stay in corridor-platform unchanged.

### 1. Flask/WSGI concurrency bridge

The runtime is async. Flask workers are sync threads. A thin shim is needed per worker:

```python
# corridor_api/ai/agent_runtime/adapters/opencode/runtime_bridge.py

import anyio
from opencode_runtime import OpenCodeRuntime

_runtime: OpenCodeRuntime | None = None

def get_runtime() -> OpenCodeRuntime:
    global _runtime
    if _runtime is None:
        _runtime = OpenCodeRuntime(
            runtime_dir=settings.OPENCODE_RUNTIME_DIR,
            config=_build_base_config(),   # model, permissions, compaction: off
        )
    return _runtime

def run_sync(coro):
    """Run an async runtime coroutine from a sync Flask worker."""
    return anyio.from_thread.run_sync_in_worker_thread(coro)
```

When corridor moves to async (FastAPI / async Flask), this shim is deleted. Nothing else changes.

### 2. LLM config resolution

Corridor maps its own provider model (Azure, Bedrock, Anthropic, etc.) to the `opencode.json`
format. This stays in `utils.py` `_build_opencode_llm_config()` — it is corridor's deployment
config, not a generic concern.

The output is passed as `config=` to `runtime.session()`:

```python
config = _build_opencode_llm_config(corridor_llm_settings)
config["permission"] = {"bash": "deny", "edit": "deny", "webfetch": "deny"}
config["tools"]      = {"bash": False, "edit": False, "write": False}
config["compaction"] = {"auto": False}

session = await runtime.session(
    workspace=workspace_name,
    user_id=user.username,
    config=config,
    env={"ANTHROPIC_API_KEY": ...},   # or Azure creds, etc.
)
```

### 3. Materials: agents, skills, AGENTS.md

corridor's agent definitions, skills, and guardrails are passed as `materials=`:

```python
runtime = OpenCodeRuntime(
    runtime_dir=settings.OPENCODE_RUNTIME_DIR,
    materials=Path(__file__).parent / "materials",
    # materials/ contains:
    #   AGENTS.md
    #   .opencode/agents/corra.md
    #   .opencode/agents/sql.md
    #   .opencode/skills/corridor-code-generation/
    #   .opencode/skills/corridor-debug/
    #   ... etc
)
```

The runtime copies these into each user's `server_dir` before the server starts.
No seed/cleanup logic needed — the runtime handles it per isolation key.

### 4. MCP sidecar (`corridor-mcp`)

The `corridor-mcp` FastMCP HTTP server is a corridor business feature. It stays exactly
as-is. The only change is how its config reaches `opencode.json` — it is now part of the
`config=` dict passed to the runtime, instead of being written by `_write_opencode_config()`:

```python
config["mcp"] = {
    "corridor-mcp": {
        "type": "remote",
        "url": f"http://127.0.0.1:{MCP_PORT}/mcp",
        "enabled": True,
        "headers": {
            "x-corridor-user":      "{env:CORRIDOR_USER}",
            "x-corridor-workspace": "{env:CORRIDOR_WORKSPACE}",
            "x-corridor-mcp-allowed-file-roots": "{env:CORRIDOR_MCP_ALLOWED_FILE_ROOTS}",
        },
    }
}
```

MCP identity env vars go into `env=`:

```python
env = {
    "CORRIDOR_USER":      user.username,
    "CORRIDOR_WORKSPACE": workspace_name,
    "CORRIDOR_MCP_ALLOWED_FILE_ROOTS": f"{input_path}:{scratch_path}",
}
```

MCP connectivity checking (the `GET /mcp` gate before streaming) stays in the adapter —
it is a corridor-specific reliability concern, not a runtime concern.

### 5. Permission handling

Two lines in the stream loop. Pass deny defaults in `config=` and reject any that slip through:

```python
# in config=
config["permission"] = {"bash": "deny", "edit": "deny", "webfetch": "deny"}

# in stream loop
async for event in session.stream(prompt):
    if event.type == "permission.asked":
        await session.raw_client.post(
            f"/permission/{event.raw['id']}/reply",
            {"reply": "reject", "message": "sandboxed workspace — use corridor-mcp tools"},
        )
        continue
    yield event
```

### 6. DB registry for multi-pod coordination

The runtime uses a flat-file registry (`~/.opencode-runtime/servers/<key>.json`).
This is sufficient for a single-process or single-pod deployment.

For corridor's multi-pod gunicorn setup (multiple API replicas, shared DB), the existing
`DbOpenCodeServerRegistry` remains necessary for cross-pod coordination (`host_id`,
`SELECT FOR UPDATE`, `lease_expires_at`).

**However:** if corridor is ever redeployed as single-pod (or moves to async with a single
event loop per pod), the DB registry becomes replaceable by the runtime's file registry,
and `server_registry.py` can be deleted entirely.

### 7. Thread / session persistence

`ChatThread`, `ChatMessage`, `ChatProviderSession`, `thread_service.py` — unchanged.
The runtime provides `session.session_id` which corridor maps to `provider_session_id`
exactly as today.

### 8. Everything else

All web endpoints, title generation, feedback, snapshots, message regeneration, Langfuse
tracing (which traces corridor's own LLM calls, not opencode) — untouched.

---

## Migration: what actually changes in the adapter

The delta is almost entirely in two files:

### `adapters/opencode/service.py`

**Delete:**
- `_default_process_factory()` — process spawning
- `_wait_for_server_ready()` — health polling
- `_start_server()` — startup orchestration
- `HttpxOpenCodeClient` class (or inline it) — replaced by `OpenCodeClient`
- `iter_sse()` — replaced by `OpenCodeClient.events()`
- The session creation block in `run()` / `run_stream()` — replaced by `session.ask()` / `session.stream()`

**Keep:**
- `_write_opencode_config()` → becomes `_build_config()` returning a dict (no file write)
- `_build_prompt()` — unchanged, passed as the message string
- `_reject_permission()` — 2-line inline in stream loop
- `_get_diff()` → `session.raw_client.get(f"/session/{id}/diff")`
- `_log_mcp_status()` — unchanged
- All event enrichment and SSE proxy logic — unchanged

### `adapters/opencode/environment.py`

**Delete the entire file.**

Everything it does is now either:
- Handled by the runtime (`HOME`, `TMPDIR`, config isolation, materials copy)
- Passed as `env=` dict (MCP identity vars)
- Passed as `materials=` (skills, auth files, agent files)
- Not needed at all (UV cache — not a problem with per-server HOME)

### `adapters/opencode/server_registry.py`

**Keep for now** (multi-pod requirement). Simplify it over time if deployment changes.
The idle timeout and in-flight tracking can be stripped — they are cost-saving
features, not isolation requirements, and the runtime can be given an equivalent
via a wrapper if needed.

---

## Complexity reduction: the numbers

| File | Lines today | Lines after |
|---|---|---|
| `environment.py` | ~330 | 0 (deleted) |
| `utils.py` (process + HTTP client parts) | ~430 | ~120 (LLM config + prompt build only) |
| `service.py` (startup + session + SSE) | ~940 | ~500 |
| `server_registry.py` | ~620 | ~620 (unchanged, simplifiable later) |
| **Total adapter** | **~2,320** | **~1,240** |

Roughly half the adapter code is deleted. The remaining half is corridor business logic
that was always corridor's responsibility — agent definitions, MCP config, LLM provider
mapping, permission policy, stream enrichment.

---

## Dependency after migration

```
pip install opencode-runtime   # adds: httpx (already a corridor dep)
```

One runtime dependency. No new infra. No new services. `opencode` binary on PATH
is already a requirement today.
