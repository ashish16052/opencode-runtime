# Raw client

`session.raw_client` gives you direct access to every OpenCode server endpoint — anything not covered by `ask()` and `stream()`. It exposes two methods: `get(path)` and `post(path, body)`, both return parsed JSON.

The examples below follow a single story: a code review backend that receives a PR, runs an agent review, tracks progress, stores the result, and handles rollback.

```python
from opencode_runtime import OpenCodeRuntime

async with OpenCodeRuntime(runtime_dir=".opencode-runtime") as r:
    session = await r.session(workspace="acme", user_id="alice")
    client = session.raw_client
```

## 1. Pick the right agent

Before running the review, confirm which agents are available and pick the one best suited for the task:

```python
agents = await client.get("/agent")
review_agents = [a for a in agents if "review" in a["name"].lower()]

# fall back to "plan" (read-only, no edits) if no dedicated reviewer
agent = review_agents[0]["name"] if review_agents else "plan"
```

## 2. Run the review

Use the chosen agent for this specific call:

```python
response = await session.ask(
    "Review this PR for security issues, logic bugs, and test coverage gaps",
    agent=agent,
)
print(response.text)
```

## 3. Track todos

For complex PRs, the agent creates a todo list as it works through the review. Poll it to show progress in your UI:

```python
todos = await client.get(f"/session/{session.session_id}/todo")
for todo in todos:
    icon = {"completed": "✓", "in_progress": "⟳", "pending": "○", "cancelled": "✗"}.get(todo["status"], "?")
    print(f"  {icon} {todo['content']}")
```

## 4. Store the full conversation

After the review, fetch the full message history to persist in your database — useful for audit trails, displaying review threads in your UI, or feeding into downstream pipelines:

```python
messages = await client.get(f"/session/{session.session_id}/message")
for entry in messages:
    info = entry["info"]
    role = info["role"]
    cost = info.get("cost", 0) if role == "assistant" else 0
    text = next((p["text"] for p in entry["parts"] if p["type"] == "text"), "")
    db.insert(session_id=session.session_id, role=role, text=text, cost=cost)
```

## 5. Revert if the agent made unwanted changes

If the agent edited files as part of the review and you want to roll them back:

```python
messages = await client.get(f"/session/{session.session_id}/message")

# revert to just before the last assistant turn
message_id = messages[-2]["info"]["id"]
await client.post(
    f"/session/{session.session_id}/revert",
    {"messageID": message_id},
)
```

## 6. Report cost

At the end of the review, read the final cost from the last assistant message for billing or quota tracking:

```python
messages = await client.get(f"/session/{session.session_id}/message")
assistant_messages = [e for e in messages if e["info"]["role"] == "assistant"]

if assistant_messages:
    last = assistant_messages[-1]["info"]
    cost = last.get("cost", 0)
    tokens = last.get("tokens", {})
    print(f"Review cost: ${cost:.5f} ({tokens.get('input', 0)} in / {tokens.get('output', 0)} out)")
```

## Full endpoint reference

See the [OpenCode server docs](https://opencode.ai/docs/server) for all endpoints and the full type definitions in [types.gen.ts](https://github.com/anomalyco/opencode/blob/dev/packages/sdk/js/src/gen/types.gen.ts).
