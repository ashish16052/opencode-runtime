# Configuration & materials

## Config

Pass any valid `opencode.json` keys as a dict. opencode-runtime writes this as the server's config before starting. **`runtime_dir` must be set for config to take effect** — without it no config file is written.

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime(
    runtime_dir=".opencode-runtime",
    config={
        "model": "anthropic/claude-sonnet-4-5",
        "permission": {"bash": "deny"},
    },
) as r:
    session = await r.session()
    response = await session.ask("Analyse the architecture")
    print(response.text)
```

See the [OpenCode config reference](https://opencode.ai/docs/config/) for all available keys.

## Materials

Pass a directory of OpenCode-native files and they are copied into the server before it starts. This is how you bring custom instructions, agents, and skills to a managed server.

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime(
    runtime_dir=".opencode-runtime",
    materials="./opencode-materials",
) as r:
    session = await r.session()
    response = await session.ask("Follow the instructions in AGENTS.md")
    print(response.text)
```

What can go in a materials directory:

| File / path | Purpose | Docs |
|---|---|---|
| `opencode.json` | Server config (model, permissions, MCP servers, …) | [Config](https://opencode.ai/docs/config/) |
| `AGENTS.md` | Rules and instructions included in every session | [Rules](https://opencode.ai/docs/rules/) |
| `.opencode/agents/` | Custom agent definitions (markdown format) | [Agents](https://opencode.ai/docs/agents/#markdown) |
| `.opencode/skills/` | Reusable skill definitions | [Agent Skills](https://opencode.ai/docs/skills/) |

## project_dir

The directory OpenCode runs against — the working directory of the subprocess. Defaults to `.` (wherever your Python process is running).

```python
async with OpenCodeRuntime(project_dir="/path/to/repo") as r:
    session = await r.session()
    response = await session.ask("What does this project do?")
    print(response.text)
```

**When to use:** when your Python backend runs from a different location than the repo you want OpenCode to work on — for example, a service that checks out customer repos to a temp directory and points OpenCode at each one.

## runtime_dir

When set, each server gets its own isolated `HOME` and config file (`opencode.json`) under `runtime_dir/servers/<key>/`. Without it, OpenCode uses your real user environment and any `config` or `materials` passed to the runtime are ignored.

```python
async with OpenCodeRuntime(runtime_dir=".opencode-runtime") as r:
    session = await r.session()
    response = await session.ask("What does this project do?")
    print(response.text)
```

**When to use:** any multi-user or production setup. Without `runtime_dir`, all servers share your real home directory — meaning shared OpenCode config, shared history, and API keys from your personal environment. Setting it gives every server a clean, isolated slate.

## Session-level overrides

`config`, `materials`, and `env` can all be overridden per session. Session config is shallow-merged with runtime config; session materials replace runtime materials; session env is merged with runtime env. Session keys win in all cases.

```python
async with OpenCodeRuntime(
    config={"model": "anthropic/claude-sonnet-4-5"},
    materials="./base-materials",
    env={"API_KEY": "default"},
) as r:
    session = await r.session(
        config={"model": "anthropic/claude-opus-4-5"},
        materials="./org-a-materials",
        env={"API_KEY": "org-a-key"},
    )
    response = await session.ask("Analyse the architecture")
    print(response.text)
```

Note: `config` and `materials` affect server identity — different values produce a separate server process. `env` does not; if the server is already running, a different `env` passed to `session()` has no effect.
