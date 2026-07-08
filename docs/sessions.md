# Sessions

## Single ask

The simplest usage — send a message, get a response:

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime() as r:
    session = await r.session()
    response = await session.ask("What does this project do?")
    print(response.text)
```

`response.text` is the full assistant reply. `response.raw` contains all raw events if you need them (tool calls, thinking, status updates).

## Multi-turn conversation

Multiple `ask()` calls on the same session continue the same conversation — OpenCode keeps the full history server-side:

```python
async with OpenCodeRuntime() as r:
    session = await r.session()
    await session.ask("Explain this repo's architecture")
    await session.ask("Which file handles authentication?")  # has full context
    response = await session.ask("What would you change about it?")
    print(response.text)
```

To start a fresh independent conversation, get a new session:

```python
async with OpenCodeRuntime() as r:
    session_a = await r.session()  # conversation A
    session_b = await r.session()  # conversation B — isolated, no shared history
```

## Resume across restarts

`session_id` is set after the first `ask()` or `stream()` call. Persist it to resume the conversation later:

```python
# First run
async with OpenCodeRuntime() as r:
    session = await r.session()
    await session.ask("Explain this repo")
    saved_id = session.session_id  # store in your DB

# Later — picks up the same conversation
async with OpenCodeRuntime() as r:
    session = await r.session(session_id=saved_id)
    response = await session.ask("Where did we get to?")
    print(response.text)
```

## Per-message overrides

`ask()` and `stream()` accept per-message overrides for model, agent, tools, and system prompt. These apply to that message only — they don't change the session's config:

```python
async with OpenCodeRuntime() as r:
    session = await r.session()

    # use a specific agent for one task
    review = await session.ask(
        "Review this PR for security issues",
        agent="security-auditor",
    )

    # override model for a single expensive call
    deep = await session.ask(
        "Redesign the auth module",
        model="anthropic/claude-opus-4-5",
    )

    # disable bash for one call
    safe = await session.ask(
        "Summarise the test coverage",
        tools={"bash": False},
    )
```

## Abort

Cancel a running session mid-flight:

```python
import asyncio
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime() as r:
    session = await r.session()

    async def run():
        await session.ask("Refactor the entire codebase")

    task = asyncio.create_task(run())
    await asyncio.sleep(5)
    await session.abort()   # cancels the in-progress session server-side
    task.cancel()
```
