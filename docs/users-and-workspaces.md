# Multi-tenant

When you embed OpenCode in a SaaS backend or internal platform, different users need different servers — each with their own workspace, conversation history, and config. Without isolation, users would share state, see each other's history, and interfere with each other's sessions.

opencode-runtime handles this automatically. Pass `user_id` to get a server per user. Add `workspace` to isolate by tenant. Each server is started on first use and reused on subsequent calls — no manual lifecycle management.

## One user

The simplest case — a single user gets their own isolated server:

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime(runtime_dir=".opencode-runtime") as r:
    session = await r.session(user_id="u_1")
    response = await session.ask("What does this project do?")
    print(response.text)
```

`runtime_dir` gives the server its own `HOME` and config directory. Without it, all users share your real environment.

## Multiple users

Each `user_id` gets its own server, history, and workspace. A SaaS product running code review for multiple developers:

```python
async with OpenCodeRuntime(runtime_dir=".opencode-runtime") as r:
    alice = await r.session(user_id="alice")
    bob   = await r.session(user_id="bob")

    # these run on separate servers — no shared state
    await alice.ask("Review my PR for security issues")
    await bob.ask("Explain the payment module")
```

Same `user_id` called again later reuses the same server and conversation history — no re-initialisation cost.

## Multiple organisations

Add `workspace` to isolate by tenant. A platform serving multiple engineering teams, each with their own codebase:

```python
async with OpenCodeRuntime(runtime_dir=".opencode-runtime") as r:
    alice_acme   = await r.session(workspace="acme",   user_id="alice")
    alice_globex = await r.session(workspace="globex", user_id="alice")

    await alice_acme.ask("List our API endpoints")
    await alice_globex.ask("List our API endpoints")  # separate server, separate history
```

Same `user_id`, different `workspace` — two fully isolated servers. `user_id` alone is not enough to isolate tenants.

## Per-tenant config and materials

Give each tenant their own model, permissions, and agent instructions. A platform where enterprise customers get different models and tighter permissions than free-tier users:

```python
async with OpenCodeRuntime(runtime_dir=".opencode-runtime") as r:
    # enterprise tenant — powerful model, custom agent instructions
    enterprise = await r.session(
        workspace="acme",
        user_id="alice",
        config={"model": "anthropic/claude-opus-4-5", "permission": {"bash": "deny"}},
        materials="./tenants/acme/opencode-materials",
    )

    # free tier — cheaper model, default instructions
    free = await r.session(
        workspace="hobby",
        user_id="bob",
        config={"model": "anthropic/claude-haiku-4-5"},
    )
```

Different `config` or `materials` produce different server keys — so `acme/alice` and `hobby/bob` run on entirely separate server processes even if `project_dir` is the same.

## Point each tenant at their own repo

A CI platform that checks out each customer's repo and runs OpenCode against it:

```python
import asyncio
from opencode_runtime import OpenCodeRuntime

async def review_repo(org: str, repo_path: str) -> str:
    async with OpenCodeRuntime(
        project_dir=repo_path,
        runtime_dir=".opencode-runtime",
    ) as r:
        session = await r.session(workspace=org)
        response = await session.ask("Review this codebase for security issues")
        return response.text

# run reviews in parallel across orgs
results = await asyncio.gather(
    review_repo("acme",   "/checkouts/acme"),
    review_repo("globex", "/checkouts/globex"),
    review_repo("initech", "/checkouts/initech"),
)
```

## How isolation works

Each server runs as a separate OS process with its own isolated directory under `runtime_dir`:

```
.opencode-runtime/
└── servers/
    ├── 39dce5beb4debfaa/   # acme / alice
    │   ├── opencode.json   # server config written from runtime config dict
    │   ├── opencode.log    # server logs
    │   └── tmp/            # temp files
    ├── 81fa29acb3e9210f/   # globex / alice
    │   ├── opencode.json
    │   ├── opencode.log
    │   └── tmp/
    └── c3f2a1d9e8b74f05/   # acme / bob
        ├── opencode.json
        ├── opencode.log
        └── tmp/
```

No server can read another's home, config, or history. The directory name is a hash of `(workspace, user_id, project_dir, materials, config)` — same inputs always produce the same directory, so servers survive restarts and resume where they left off.

Isolation is at the process and filesystem level. If your use case allows users to run arbitrary shell commands via the `bash` tool, run each server in its own container for a stronger boundary.
