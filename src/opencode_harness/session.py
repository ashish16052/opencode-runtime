from __future__ import annotations

import asyncio
import typing as t

from .event import OpenCodeEvent
from .response import OpenCodeResponse

if t.TYPE_CHECKING:
    from .client import OpenCodeClient


class OpenCodeSession:
    """A conversation session with an OpenCode server.

    Sessions are self-contained — they hold their own client, config, and
    conversation state. Obtain via ``OpenCodeHarness.session()``.

    Args:
        client:      The HTTP client for the server backing this session.
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
        self.session_id = session_id
        self.config = config
        self.env = env

        self._oc_session_id: str | None = None  # set after POST /session

    @property
    def raw_client(self) -> OpenCodeClient:
        """The HTTP client for the server backing this session."""
        return self._client

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
        # Create an OpenCode session server-side if we don't have one yet
        if self._oc_session_id is None:
            result = await self._client.post("/session", {})
            self._oc_session_id = result["id"]

        oc_session_id = self._oc_session_id
        assert oc_session_id is not None

        send_task = asyncio.create_task(
            self._client.send(
                oc_session_id,
                message,
                model=model,
                agent=agent,
                tools=tools,
                system=system,
            )
        )

        try:
            async for event in self._client.events(oc_session_id):
                yield event
        finally:
            if not send_task.done():
                send_task.cancel()
                try:
                    await send_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def abort(self) -> None:
        """Abort a running session."""
        if self._oc_session_id is not None:
            await self._client.post(f"/session/{self._oc_session_id}/abort", {})

    async def close(self) -> None:
        """Release conversation-level resources."""
        self._oc_session_id = None
