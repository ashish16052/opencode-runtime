# opencode-runtime

Python runtime for deploying and managing OpenCode instances at scale.

---

Running OpenCode for a single developer is simple.

Running OpenCode for many users, isolated repositories, persistent workspaces,
multiple OpenCode instances, and production workloads requires infrastructure.

OpenCode Runtime provides that infrastructure.

**Use this when you need to:**
- Run OpenCode for multiple users or teams from a Python backend
- Give each user an isolated workspace with no shared state
- Embed OpenCode in a SaaS product or internal platform
- Manage OpenCode instance lifecycles (start, health-check, reuse, stop)
- Stream OpenCode responses to your application in real time

**What it provides:**
- **One instance per user** — automatically started, isolated, and reused
- **Filesystem isolation** — each user gets a private workspace; no shared state
- **Lifecycle management** — health-checked startup, graceful shutdown, stale process recovery
- **Streaming** — consume every OpenCode event as it arrives
- **Native OpenCode config** — your existing `opencode.json`, agents, and skills drop in unchanged

## Install

```sh
pip install opencode-runtime
```

Requires `opencode` on PATH:

```sh
npm install -g opencode-ai
```

## Usage

### Ask

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime() as r:
    session = await r.session()
    response = await session.ask("Explain this repo")
    print(response.text)
```

### Config

Pass a raw `opencode.json` dict to control model, permissions, and any other OpenCode-native setting:

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime(
    config={"model": "anthropic/claude-sonnet-4-5", "permission": {"bash": "deny"}},
) as r:
    session = await r.session()
    response = await session.ask("Analyse the architecture")
    print(response.text)
```

### Materials

Pass a directory of OpenCode-native files — `AGENTS.md`, `opencode.json`, `.opencode/skills/`, etc. — and they are copied into the server before it starts:

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime(materials="./opencode-materials") as r:
    session = await r.session()
    response = await session.ask("Follow the instructions in AGENTS.md")
    print(response.text)
```

### Isolation

Set `project_dir` and `runtime_dir` to give the server its own `HOME`, config, and conversation history — separate from your real environment:

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime(
    project_dir="/path/to/project",
    runtime_dir=".opencode-runtime",
    materials="./opencode-materials",
) as r:
    session = await r.session()
    response = await session.ask("What does this project do?") 
    print(response.text)
```

### Per-user sessions

Each unique `user_id` gets its own isolated server and conversation history:

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime(runtime_dir=".opencode-runtime") as r:
    session = await r.session(user_id="u_1")
    response = await session.ask("What does this project do?")
    print(response.text)
```

### Multi-tenant

Add `workspace` to isolate by tenant. Different `(workspace, user_id)` → different server. Same combination → server reused:

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime(runtime_dir=".opencode-runtime") as r:
    s1 = await r.session(workspace="org_a", user_id="u_1")
    s2 = await r.session(workspace="org_b", user_id="u_2")
    r1 = await s1.ask("What does this project do?")
    r2 = await s2.ask("List the main dependencies")
```

### Session continuation

Multiple `ask()` calls on the same session continue the same conversation — OpenCode keeps the full history server-side:

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime() as r:
    session = await r.session()
    await session.ask("Explain this repo")
    await session.ask("Which file should I start with?")  # has full context
```

To resume a conversation in a future session, store `session.session_id` and pass it back:

```python
# First session
async with OpenCodeRuntime() as r:
    session = await r.session()
    await session.ask("Explain this repo")
    saved_id = session.session_id  # persist this

# Later — resumes the same conversation
async with OpenCodeRuntime() as r:
    session = await r.session(session_id=saved_id)
    await session.ask("What were we discussing?")
```

### Raw client

Access any OpenCode server endpoint directly:

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime() as r:
    session = await r.session()
    agents = await session.raw_client.get("/agent")
    mcp    = await session.raw_client.get("/mcp")
```

### Streaming

For live output, use `stream()`. It yields every event OpenCode emits as an
`OpenCodeEvent` with three fields: `type` (event kind), `text` (populated for
text-bearing events, `None` otherwise), and `raw` (full server payload). See
the [OpenCode server docs](https://opencode.ai/docs/server#events) for all event types.

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime() as r:
    session = await r.session()
    async for event in session.stream("Review this PR"):
        if event.type == "message.part.delta" and event.text:
            print(event.text, end="", flush=True)
```

## CLI

`opencode-runtime` ships with a CLI for managing opencode servers from the terminal. Useful for inspecting what your application is running, debugging sessions, or managing servers independently.

### Start a server

```sh
opencode-runtime serve
```

The server runs in the background. Use `ps`, `stop`, and `health` to manage it.

### Multi-tenant

Each unique `(workspace, user-id)` combination gets its own isolated server:

```sh
opencode-runtime serve --workspace org_a --user-id u_1
opencode-runtime serve --workspace org_b --user-id u_2
```

### List servers

```sh
opencode-runtime ps
```

```
  ID                  PID    PORT    STATUS     UPTIME    WORKSPACE   USER    PROJECT
  ──────────────────────────────────────────────────────────────────────────────────
  39dce5beb4debfaa   12051   58409   ● alive    Up 5m     org_a       u_1     ~/Developer/myproject
  81fa29acb3e9210f   12088   58411   ● alive    Up 3m     org_b       u_2     ~/Developer/myproject
```

### Check health

```sh
opencode-runtime health 39dce5beb4debfaa
```

### Stop a server

```sh
opencode-runtime stop 39dce5beb4debfaa
```

### Stop all servers

```sh
opencode-runtime stop-all
```

### Library + CLI

Start a server from Python, then inspect and manage it from the terminal:

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime() as r:
    session = await r.session()
    response = await session.ask("Review this PR")
    print(response.text)
```

```sh
# while app.py is running
opencode-runtime ps
opencode-runtime health 39dce5beb4debfaa
opencode-runtime stop   39dce5beb4debfaa
```

## Requirements

- Python 3.10+
- `opencode` 1.0+ on PATH

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md).

## License

Apache 2.0
