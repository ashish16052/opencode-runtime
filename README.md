# opencode-runtime

**Embed OpenCode in your Python application.**

OpenCode is a great CLI coding agent. But to embed it in a product, SaaS
backend, or multi-user automation system, you need more than a CLI process.

opencode-runtime turns OpenCode into a managed application runtime for
Python. It uses OpenCode as the agent harness and adds what you need to
run it as part of an application: lifecycle management, per-user
workspaces, reusable sessions, health checks, streaming output, and
multi-server orchestration.

## Install

```sh
pip install opencode-runtime
npm install -g opencode-ai   # opencode must be on PATH
```

## Use

### Your first session

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime() as r:
    session = await r.session()
    response = await session.ask("Explain this repo")
    print(response.text)
```

### One server per user, automatically

Every `(workspace, user_id)` pair gets its own isolated server, workspace, and conversation history — started on first use, reused after:

```python
async with OpenCodeRuntime(runtime_dir=".opencode-runtime") as r:
    s1 = await r.session(workspace="org_a", user_id="u_1")
    s2 = await r.session(workspace="org_b", user_id="u_2")
```

### Stream every token as it arrives

```python
async for event in session.stream("Review this PR"):
    if event.type == "message.part.delta" and event.text:
        print(event.text, end="", flush=True)
```

## Inspect

Servers started from Python are visible and manageable from the terminal:

```sh
opencode-runtime ps
```

```
  ID                  PID    PORT    STATUS      UPTIME    WORKSPACE   USER    PROJECT
  ───────────────────────────────────────────────────────────────────────────────────
  39dce5beb4debfaa   12051   58409   ● running   5m        org_a       u_1     ~/Developer/myproject
  81fa29acb3e9210f   12088   58411   ● running   3m        org_b       u_2     ~/Developer/myproject
```

```sh
opencode-runtime health 39dce5beb4debfaa
opencode-runtime inspect 39dce5beb4debfaa
opencode-runtime stop-all
```

## Guides

- [OpenCode config](docs/opencode-config.md) — models, permissions, agents, skills
- [Sessions](docs/sessions.md) — continuation, resume across restarts
- [Users & workspaces](docs/users-and-workspaces.md) — multi-tenant isolation
- [Streaming](docs/streaming.md) — event types, tool calls, permissions, cost
- [CLI](docs/cli.md) — manage your fleet from the terminal
- [HTTP client](docs/http-client.md) — direct server access

## Requirements

- Python 3.10+
- `opencode` 1.0+ on PATH

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md).

## License

Apache 2.0
