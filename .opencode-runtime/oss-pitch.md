# OSS Pitch: opencode-runtime

Proposal to open-source `opencode-runtime` and adopt it in corridor-platform.

---

## What it is

A Python library that turns the `opencode` CLI into a production-ready backend component:
managed process lifecycle, per-user isolation, session multiplexing, and SSE streaming —
one `pip install`, one runtime dependency (`httpx`).

---

## The core argument

**We built this infra twice. Once is enough, and the second copy should not live inside
a product service.**

corridor-platform's opencode adapter (`adapters/opencode/`) contains roughly 2,300 lines
across four files. Of that, approximately half — `environment.py` (330 lines), the process
management and HTTP client in `utils.py` (~300 lines), and the server startup and session
wiring in `service.py` (~200 lines) — is generic infrastructure: spawn a process, isolate
it per user, poll health, relay SSE. It has nothing to do with corridor's agents, data,
or business rules.

That infrastructure is what `opencode-runtime` is. The library exists because we needed
it, we built it cleanly, and it works. The question is only whether we maintain it inside
the product codebase or as a proper library.

---

## What open-sourcing actually costs

Nothing proprietary leaves the codebase.

The library contains:
- Process lifecycle management (spawn, health-poll, stop)
- Per-user filesystem isolation (one `server_dir` per key, isolated `HOME`)
- HTTP + SSE client wrapper
- Flat-file server registry
- CLI for development observability

The library does **not** contain:
- corridor's agent definitions (`corra.md`, `sql.md`, `AGENTS.md`)
- corridor's skills (`corridor-code-generation`, etc.)
- corridor-mcp or any MCP tooling
- corridor's LLM provider config or API keys
- corridor's thread/session/message persistence
- corridor's web API or any business logic

Everything that makes corridor corridor stays in corridor. What we're open-sourcing is the
plumbing, not the product.

---

## What we gain

### 1. Half the adapter is deleted

`environment.py` is deleted entirely. The process factory, health poller, HTTP client,
and session wiring in `service.py` and `utils.py` are deleted. What remains is corridor
business logic: LLM config resolution, agent file management, MCP config, permission
policy, stream enrichment, thread persistence.

Before: ~2,300 lines of mixed infrastructure + business logic.
After: ~1,240 lines of pure business logic.

That is not just fewer lines. It is a simpler mental model. Today, a new engineer reading
`service.py` has to understand process management, port allocation, health polling, SSE
parsing, *and* corridor's agent logic — all in the same file. After migration, `service.py`
only contains corridor decisions.

### 2. The infrastructure is maintained by the library, not by us

Every time opencode changes its server API, its auth mechanism, or its event format,
we currently absorb that change in corridor's adapter. After migration, `opencode-runtime`
absorbs it. If the broader community is using the library, the surface area of people
catching and reporting those breaks is larger than our team alone.

### 3. Regression testing moves to the library

`opencode-runtime` has 8 test files covering process lifecycle, multi-tenant isolation, registry
correctness, and CLI behavior. Today, corridor has no equivalent tests for the opencode
adapter's process management — it is too entangled with Flask/DB context to unit test.
As a standalone library, all of that is testable in isolation with a real opencode binary.

### 4. We own the canonical Python integration for opencode

opencode is growing. There is no official Python library for embedding it in a backend.
If we publish `opencode-runtime`, corridor becomes the reference implementation for this
use case. That is ecosystem leverage — other teams building on opencode in Python will
depend on something we own and maintain, rather than building their own versions of the
same process management code.

### 5. Engineering signal

A well-tested, zero-bloat Python library published on PyPI is a concrete artifact of
engineering quality. It is useful for hiring conversations and demonstrates that corridor
invests in the broader ecosystem rather than only consuming it.

---

## Risks and honest answers

**"We lose control of the roadmap."**
We are the primary maintainer. We control what goes in. OSS means anyone can file issues
and submit PRs — that is free labour, not a governance problem.

**"Competitors can use it."**
Yes, and they could already build it themselves in a weekend. The process management code
is not a competitive advantage. Our agents, data integrations, and product are.

**"We have to support external users."**
The library has a narrow scope (process lifecycle + SSE relay) and a stable interface.
Support burden will be minimal. We can be explicit about what we do and do not commit to.

**"What if opencode changes their API?"**
That risk exists whether or not we open-source. The library actually reduces this risk
by centralising the adaptation point rather than scattering it across corridor's adapter.

---

## What approval enables

1. Publish `opencode-runtime` to PyPI under the existing Apache 2.0 license.
2. Replace corridor's `environment.py`, process factory, and HTTP client with `pip install opencode-runtime`.
3. Delete ~1,100 lines of infrastructure code from corridor-platform.
4. All future opencode API changes are handled in one place, not in corridor's adapter.

---

## Ask

Approval to:
1. Make the `opencode-runtime` repository public.
2. Publish to PyPI.
3. Add it as a dependency to `corridor-api` and migrate the adapter.
