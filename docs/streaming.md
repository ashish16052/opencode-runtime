# Streaming

Use `stream()` instead of `ask()` to consume every OpenCode event as it arrives. `ask()` is built on top of `stream()` — it just accumulates the text for you.

## Basic stream

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime() as r:
    session = await r.session()
    async for event in session.stream("Review this PR"):
        print(event.type, event.text)
```

Each event is an `OpenCodeEvent` with three fields:

| Field | Type | Description |
|---|---|---|
| `type` | `str` | Event kind — see table below |
| `text` | `str \ None` | Populated for text-bearing events, `None` otherwise |
| `raw` | `Any` | Full server payload — use this when you need fields beyond `type` and `text` |

## Event types

Common events emitted by OpenCode. Full type definitions in [types.gen.ts](https://github.com/anomalyco/opencode/blob/dev/packages/sdk/js/src/gen/types.gen.ts).

| Type | `text` | Description |
|---|---|---|
| `message.part.delta` | Token string | Incremental token — fires for each chunk of text as the model streams it |
| `message.part.updated` | Text or `None` | Full part snapshot — fires when a part is complete (`text`, `tool`, `reasoning`, …); `text` is the entire accumulated content |
| `message.updated` | `None` | Cumulative cost and token counts for the assistant message |
| `session.status` | `None` | Status change — `busy`, `idle` or `retry` (with `attempt` and `message`) |
| `session.idle` | `None` | Terminal — model finished, stream ends |
| `session.error` | `None` | Terminal — error occurred, stream ends; details in `event.raw` |
| `permission.asked` | `None` | Agent blocked waiting for tool approval — must respond to unblock |
| `permission.replied` | `None` | Permission request was answered |

## Print tokens as they arrive

Filter to `message.part.delta` events to print text token by token as the model streams:

```python
async for event in session.stream("Explain the auth module"):
    if event.type == "message.part.delta" and event.text:
        print(event.text, end="", flush=True)
```

## Tool call visibility

Track tool calls as they run alongside the agent's text output:

```python
async for event in session.stream("Summarize the last 5 commits"):
    # tool calls
    if event.type == "message.part.updated":
        part = (event.raw.get("properties") or {}).get("part") or {}
        if part.get("type") == "tool":
            state = part["state"]
            print(f"\n{part['tool']} ({state['status']})")
            if state.get("title"):
                print(f"  [input]  {state['title']}")
            if (state.get("metadata") or {}).get("output"):
                print(f"  [output] {state['metadata']['output']}")
    # agent output
    if event.type == "message.part.delta" and event.text:
        print(event.text, end="", flush=True)
```

## Handle errors

`session.error` is a terminal event — the stream ends after it. The error payload is structured; extract the message via `error["data"]["message"]`.

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime(
    runtime_dir="/tmp/test-error",
    config={"model": "anthropic/claude-sonnet-4-5"},
    env={"ANTHROPIC_API_KEY": "sk-ant-invalid"},
) as r:
    session = await r.session()
    async for event in session.stream("Hello"):
        if event.type == "message.part.delta" and event.text:
            print(event.text, end="", flush=True)

        elif event.type == "session.error":
            error = (event.raw.get("properties") or {}).get("error") or {}
            name = error.get("name", "UnknownError")
            if name == "MessageOutputLengthError":
                print("\n[error: output length exceeded]")
            else:
                message = (error.get("data") or {}).get("message", "unknown error")
                print(f"\n[error: {name} — {message}]")
```

## Handle permission requests

When the agent needs to run a tool that requires approval, it emits `permission.asked` and blocks until you respond. Valid response values are `"once"`, `"always"`, and `"reject"`:

```python
import asyncio
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime(
    runtime_dir="/tmp/my-runtime",
    config={"permission": {"edit": "ask"}},
) as r:
    session = await r.session()
    loop = asyncio.get_running_loop()

    async for event in session.stream("Refactor and run the tests"):
        if event.type == "message.part.delta" and event.text:
            print(event.text, end="", flush=True)

        elif event.type == "permission.asked":
            props = event.raw.get("properties") or {}
            filepath = (props.get("metadata") or {}).get("filepath", props.get("permission", ""))
            choice = await loop.run_in_executor(None, input, f"\nAllow {filepath}? [once/always/reject]: ")
            choice = choice.strip().lower()
            if choice not in ("once", "always", "reject"):
                choice = "reject"
            await session.raw_client.post(
                f"/session/{session.session_id}/permissions/{props.get('id')}",
                {"response": choice},
            )
```

## Track cost

`message.updated` fires throughout the turn with cumulative cost and token counts. Overwrite on each event — the last one before `session.idle` has the final totals. `cost` is in USD:

```python
cost = 0.0
tokens_in, tokens_out = 0, 0

async for event in session.stream("Explain this project"):
    if event.type == "message.part.delta" and event.text:
        print(event.text, end="", flush=True)

    elif event.type == "message.updated":
        info = (event.raw.get("properties") or {}).get("info") or {}
        if info.get("role") == "assistant":
            cost = info.get("cost", cost)
            toks = info.get("tokens") or {}
            tokens_in = toks.get("input", tokens_in)
            tokens_out = toks.get("output", tokens_out)

print(f"\nCost: ${cost:.5f} | tokens in: {tokens_in}  out: {tokens_out}")
```
