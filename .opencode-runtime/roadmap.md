# opencode-runtime: roadmap notes

Gaps, simplifications, and good-to-have capabilities — in priority order.
Goal: stay lean. Every addition should earn its place.

---

## Gaps (things that are missing today)

### 1. Concurrency safety in `get_or_start`

**What:** two async tasks calling `get_or_start` with the same key simultaneously can
both see no live server and race to spawn two processes. In a single asyncio event loop
this doesn't happen (cooperative scheduling), but it becomes real the moment someone
wraps the runtime with `anyio.to_thread` or uses it from multiple threads.

**Fix:** an asyncio `Lock` keyed by isolation key, held for the duration of the
registry-read → spawn → registry-write path. Small, no new dependencies.

---

### 2. `get_or_start` does not re-validate config on server reuse

**What:** the registry records the key (a hash of `workspace + user_id + config`).
If a server is running and you call `get_or_start` with the same key but a mutated
config dict that hashes identically (unlikely but possible with floats/ordering), the
stale server is returned. More practically: a server started outside the library
(e.g. `opencode-runtime serve` CLI) with different actual config will be reused
because the key matches.

**Fix / accepted tradeoff:** document that the key is the contract. If config must
change, callers should `stop()` the old key explicitly. No code change needed — just
clearer docs.

---

### 3. No idle / resource cleanup

**What:** instances started by the runtime run until the process exits or `stop()` is
called. In long-running backends (web servers, daemons), idle servers accumulate and
hold memory.

**Fix (if needed):** optional `idle_timeout` parameter on `OpenCodeRuntime`. After the
last session on a key goes idle, start a countdown; if no new session arrives, call
`stop(key)`. Implement as a per-key `asyncio.Task` with a `CancelledError` guard.
Keep it opt-in — not every caller needs it.

---

### 4. `to_sse()` on `OpenCodeEvent` drops non-text events

**What:** `event.to_sse()` emits `event: {type}\ndata: {text}\n\n` — only the text
field. Tool call events, thinking, status changes, and permission requests all lose
their payload. Any caller using `to_sse()` to proxy the stream to a frontend loses
that data.

**Fix:** change `to_sse()` to serialize `raw` as the data field, not `text`:
```python
def to_sse(self) -> str:
    return f"event: {self.type}\ndata: {json.dumps(self.raw)}\n\n"
```
Non-breaking if no external callers depend on the current text-only format.

---

### 5. Health poll is too slow on fast machines

**What:** `_wait_healthy` polls every 1.0 second. opencode typically starts in
200–400ms in a warm environment. The first successful health check is therefore
~1s after the process is ready — wasted latency on every cold start.

**Fix:** poll every 100ms for the first 3s, then back off to 500ms. Same budget,
faster happy path.

---

## Simplifications (things to clean up)

### 6. `OPENCODE_CONFIG_HOME` is redundant when `HOME` is already isolated

**What:** `server.py:330` sets both `HOME=server_dir` and
`OPENCODE_CONFIG_HOME=server_dir`. When `HOME` is overridden, opencode derives
its config home from `HOME` anyway. The explicit `OPENCODE_CONFIG_HOME` is
belt-and-suspenders.

**Action:** keep it — it makes the isolation intent explicit and costs nothing.
Worth a comment explaining why both are set.

---

### 7. `OpenCodeSession.close()` just sets `session_id = None`

**What:** `close()` does no cleanup on the server side — it only resets local state.
The opencode server-side session persists. The method name implies more than it does.

**Fix:** either rename to `reset()` to match what it actually does, or remove it
entirely (callers can just instantiate a new session). Leaning toward remove — it is
not useful in practice.

---

### 8. Registry directory is hardcoded to `~/.opencode-runtime/servers/`

**What:** `registry.py:20` hardcodes `REGISTRY_DIR`. When `runtime_dir` is set,
it is natural to expect the registry to live there too. Two separate locations
(runtime state under `runtime_dir/servers/<key>/`, registry under `~/.opencode-runtime/`)
is surprising.

**Fix:** make `REGISTRY_DIR` configurable — default to `~/.opencode-runtime/servers/`
  but respect an env var (`OPENCODE_RUNTIME_REGISTRY_DIR`) or a `ServerManager`
constructor parameter. Low priority since the current behaviour works.

---

## Good to have (future, opt-in, no scope creep)

### 9. Sync wrapper

**What:** the entire public API is `async`. Callers in sync contexts (Flask, scripts,
notebooks) need `anyio.from_thread.run_sync_in_worker_thread` boilerplate.

**Add:** a `SyncOpenCodeRuntime` thin wrapper that calls `anyio.from_thread.run()`
internally. Lives in a separate `opencode_runtime.sync` module so async users import
nothing extra. `anyio` would become an optional dependency (`pip install opencode-runtime[sync]`).

---

### 10. `session.diff()` convenience method

**What:** `GET /session/{id}/diff` is a commonly needed endpoint (changed files after
a run) but callers must use the `raw_client` escape hatch today.

**Add:**
```python
async def diff(self) -> list[dict]:
    if self.session_id is None:
        return []
    return await self._client.get(f"/session/{self.session_id}/diff")
```
Two lines. Makes the most common post-run operation first-class without adding a
dependency or a new abstraction.

---

### 11. `permission.asked` helper on `OpenCodeSession`

**What:** the most common response to `permission.asked` events in a backend is
auto-reject. Today this requires digging into `event.raw` and calling `raw_client.post`.

**Add:**
```python
async def reject_permission(self, event: OpenCodeEvent) -> None:
    request_id = event.raw.get("properties", {}).get("requestID")
    if request_id:
        await self._client.post(f"/permission/{request_id}/reply", {"reply": "reject"})
```
Opt-in convenience. No forced behaviour change.

---

### 12. `OpenCodeRuntime` as a WSGI/ASGI lifespan context

**What:** frameworks like FastAPI and Starlette have lifespan hooks. There is no
out-of-the-box way to wire `OpenCodeRuntime` into those lifecycles.

**Add:** a `lifespan()` async context manager that integrates with the standard
`asynccontextmanager` pattern, documented in the README with a FastAPI example.
No new code — just documentation of the existing `async with OpenCodeRuntime()` pattern
used correctly inside a lifespan function.

---

## What will NOT be added

To stay lean, the library will not grow into:

- **DB-backed registry** — that is a deployment concern for callers with multi-pod
  requirements (corridor keeps its own). The file registry is correct for the library's
  scope.
- **Provider config mapping** — translating Azure/Bedrock/Anthropic settings to
  `opencode.json` format is application-specific. Callers pass `config=` directly.
- **MCP management** — MCP servers are passed as config. The runtime is not an
  orchestrator for other processes.
- **Thread/session persistence** — caller concern. The runtime exposes `session_id`
  for callers to persist however they choose.
- **Logging framework integration** — the library emits nothing to any logger today.
  Add a single `logging.getLogger("opencode_runtime")` debug call in `_start()` and
  `get_or_start()` if requested, but no structured logging framework.
