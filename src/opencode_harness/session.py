from __future__ import annotations

import typing as t

from .event import OpenCodeEvent
from .response import OpenCodeResponse

if t.TYPE_CHECKING:
    from .client import OpenCodeClient


class OpenCodeSession:
    """A conversation session with an OpenCode server.

    Sessions are self-contained — they hold their own client, config, and
    conversation state. Obtain via ``OpenCodeHarness.session()``.

    Each session maps to a single OpenCode server-side session. Calling
    ``ask()`` or ``stream()`` multiple times on the same
    ``OpenCodeSession`` instance sends follow-up messages within the same
    conversation thread — OpenCode maintains the full history and context
    between turns.

    To start a new independent conversation, obtain a new session via
    ``OpenCodeHarness.session()``.

    Args:
        client:      The HTTP client for the server backing this session.
        workspace:   Logical tenant/workspace name, e.g. ``"acme"``.
        user_id:     Application user id, e.g. ``"u_123"``.
        session_id:  OpenCode server-side session ID. Pass an existing ID to
                     resume a previous conversation; omit to start a new one.
                     Readable after the first ``ask()`` / ``stream()`` call.
        config:      Merged OpenCode config dict for this session.
        env:         Environment variable overrides for this session.
    """

    def __init__(
        self,
        *,
        client: OpenCodeClient,
        workspace: str | None,
        user_id: str | None,
        session_id: str | None,
        config: dict[str, t.Any],
        env: dict[str, str],
    ) -> None:
        self._client = client
        self.workspace = workspace
        self.user_id = user_id
        self.session_id = session_id  # None until first stream(); set to resume
        self.config = config
        self.env = env

    @property
    def raw_client(self) -> OpenCodeClient:
        """The HTTP client for the server backing this session."""
        return self._client

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def ask(self, message: str, **kwargs: t.Any) -> OpenCodeResponse:
        """Send a message and collect the full response.

        Accumulates incremental text from ``message.part.delta`` events and
        returns a single :class:`OpenCodeResponse` when the session goes idle.
        All events (tool calls, thinking, status updates, etc.) are preserved
        in ``OpenCodeResponse.raw`` for inspection.
        """
        from .exceptions import OpenCodeServerError

        text = ""
        raw_events: list[t.Any] = []

        async for event in self.stream(message, **kwargs):
            raw_events.append(event.raw)
            if event.type == "session.error":
                raise OpenCodeServerError(event.text or "unknown error from opencode server")
            if event.type == "message.part.delta" and event.text:
                text += event.text

        return OpenCodeResponse(text=text, raw=raw_events)

    async def stream(
        self,
        message: str,
        *,
        model: str | None = None,
        agent: str | None = None,
        tools: dict[str, bool] | None = None,
        system: str | None = None,
        **kwargs: t.Any,
    ) -> t.AsyncIterator[OpenCodeEvent]:
        """Send a message and stream all events as they arrive.

        Yields every :class:`OpenCodeEvent` emitted by OpenCode for this
        session — text deltas, tool calls, thinking, status updates,
        permission requests, and terminal events. No filtering or
        interpretation is applied; callers decide what to handle.

        Calling ``stream()`` again on the same session after the first
        stream completes sends a follow-up message in the same conversation
        thread — full history is preserved server-side.

        The stream terminates automatically on ``session.idle`` or
        ``session.error``.

        Args:
            message: The user message to send.
            model:   Override the model, e.g. ``"anthropic/claude-sonnet-4-5"``.
            agent:   Override the agent.
            tools:   Per-tool enable/disable map, e.g. ``{"bash": False}``.
            system:  Additional system prompt text.
        """
        # Create an OpenCode session server-side if we don't have one yet.
        # If session_id was provided at construction, skip creation — resume
        # the existing conversation.
        if self.session_id is None:
            result = await self._client.post("/session", {})
            self.session_id = result["id"]

        session_id = self.session_id
        assert session_id is not None

        # Send the prompt first — prompt_async returns immediately once the
        # server has accepted the message. Starting the SSE stream before
        # send() completes means we risk missing early events.
        await self._client.send(
            session_id,
            message,
            model=model,
            agent=agent,
            tools=tools,
            system=system,
        )

        async for event in self._client.events(session_id):
            yield event

    async def abort(self) -> None:
        """Abort a running session."""
        if self.session_id is not None:
            await self._client.post(f"/session/{self.session_id}/abort", {})

    async def close(self) -> None:
        """Release conversation-level resources."""
        self.session_id = None
