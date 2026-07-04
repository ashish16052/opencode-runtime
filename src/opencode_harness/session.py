from __future__ import annotations

import typing as t

if t.TYPE_CHECKING:
    from .harness import OpenCodeHarness

from .event import OpenCodeEvent
from .response import OpenCodeResponse


class OpenCodeSession:
    """A lightweight conversation wrapper backed by an ``OpenCodeHarness``.

    Sessions delegate all runtime concerns (server, client, config) back to
    the harness. The session itself only owns conversation-level state:
    workspace, user_id, session_id, and per-session config/env overrides.

    Obtain via ``OpenCodeHarness.session()`` or the convenience helpers
    ``OpenCodeHarness.ask()`` / ``OpenCodeHarness.stream()``.

    Args:
        harness:     The harness that owns the server and client.
        workspace:   Logical tenant/workspace name, e.g. ``"acme"``.
        user_id:     Application user id, e.g. ``"u_123"``.
        session_id:  External correlation id stored in metadata. OpenCode
                     generates its own internal session id server-side.
        config:      Merged OpenCode config dict for this session.
        env:         Environment variable overrides for this session.
    """

    def __init__(
        self,
        *,
        harness: OpenCodeHarness,
        workspace: str | None,
        user_id: str | None,
        session_id: str | None,
        config: dict[str, t.Any],
        env: dict[str, str],
    ) -> None:
        self._harness = harness
        self.workspace = workspace
        self.user_id = user_id
        self.session_id = session_id
        self.config = config
        self.env = env

        self._oc_session_id: str | None = None  # set after POST /session

    @property
    def raw_client(self):
        """The HTTP client owned by the harness. None until server is started."""
        return self._harness._client

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def ask(self, message: str, **kwargs: t.Any) -> OpenCodeResponse:
        """Send a message and collect the full response.

        Accumulates all ``message.delta`` text from the stream and returns a
        single :class:`OpenCodeResponse`.
        """
        from .exceptions import OpenCodeServerError

        text = ""
        raw_events: list[t.Any] = []

        async for event in self.stream(message, **kwargs):
            raw_events.append(event.raw)
            if event.type == "error":
                raise OpenCodeServerError(event.text or "unknown error from opencode server")
            if event.type == "message.delta" and event.text:
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
        """Send a message and stream events as they arrive.

        Yields :class:`OpenCodeEvent` objects. Use ``event.text`` for delta
        text and ``event.raw`` for the full server payload.

        Args:
            message: The user message to send.
            model:   Override the model, e.g. ``"anthropic/claude-sonnet-4-5"``.
            agent:   Override the agent.
            tools:   Per-tool enable/disable map, e.g. ``{"bash": False}``.
            system:  Additional system prompt text.
        """
        from .exceptions import OpenCodeServerError

        client = self.raw_client
        if client is None:
            raise OpenCodeServerError(
                "No opencode server running. "
                "Use 'async with OpenCodeHarness(...) as h' to start one."
            )

        # Create an OpenCode session server-side if we don't have one yet
        if self._oc_session_id is None:
            result = await client.post("/session", {})
            self._oc_session_id = result["id"]

        # Narrowed local — type checker sees str, not str | None
        oc_session_id = self._oc_session_id
        assert oc_session_id is not None

        # Subscribe to SSE stream first, then fire the message.
        # This avoids a race where events arrive before we start listening.
        import asyncio

        send_task = asyncio.create_task(
            client.send(
                oc_session_id,
                message,
                model=model,
                agent=agent,
                tools=tools,
                system=system,
            )
        )

        try:
            async for event in client.events(oc_session_id):
                yield event
        finally:
            # Ensure send task is awaited even if caller breaks early
            if not send_task.done():
                send_task.cancel()
                try:
                    await send_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def abort(self) -> None:
        """Abort a running session."""
        if self.raw_client is not None and self._oc_session_id is not None:
            await self.raw_client.post(f"/session/{self._oc_session_id}/abort", {})

    async def close(self) -> None:
        """Release conversation-level resources."""
        self._oc_session_id = None
