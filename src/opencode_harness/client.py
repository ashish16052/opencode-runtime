from __future__ import annotations

import base64
import json
import typing as t

import httpx

from .event import OpenCodeEvent
from .exceptions import OpenCodeServerError, OpenCodeTimeoutError


class OpenCodeClient:
    """Minimal HTTP/SSE client for the OpenCode server.

    Covers only what the harness needs to function:
    - health check
    - fire-and-forget message send
    - SSE event stream

    For anything else use the ``get()`` / ``post()`` escape hatches directly
    against the OpenCode REST API (see https://opencode.ai/docs/server).

    Args:
        base_url:  Base URL of the running opencode server,
                   e.g. ``"http://127.0.0.1:4096"``.
        password:  Value of ``OPENCODE_SERVER_PASSWORD``. Sent as
                   ``Authorization: Bearer <password>`` when set.
        timeout:   Default request timeout in seconds.
    """

    def __init__(
        self,
        *,
        base_url: str,
        password: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.password = password
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Escape hatches — use these for any endpoint not covered below
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.password:
            # OpenCode uses HTTP Basic auth: username "opencode", password is the token
            credentials = base64.b64encode(f"opencode:{self.password}".encode()).decode()
            headers["Authorization"] = f"Basic {credentials}"
        return headers

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers(),
            timeout=self.timeout,
        )

    async def get(self, path: str) -> t.Any:
        """GET any OpenCode server endpoint. Returns parsed JSON."""
        async with self._http() as http:
            try:
                r = await http.get(path)
                r.raise_for_status()
                return r.json()
            except httpx.TimeoutException as exc:
                raise OpenCodeTimeoutError(f"GET {path} timed out") from exc
            except httpx.HTTPStatusError as exc:
                raise OpenCodeServerError(
                    f"GET {path} returned {exc.response.status_code}"
                ) from exc

    async def post(self, path: str, body: dict[str, t.Any]) -> t.Any:
        """POST any OpenCode server endpoint. Returns parsed JSON."""
        async with self._http() as http:
            try:
                r = await http.post(path, json=body)
                r.raise_for_status()
                return r.json()
            except httpx.TimeoutException as exc:
                raise OpenCodeTimeoutError(f"POST {path} timed out") from exc
            except httpx.HTTPStatusError as exc:
                raise OpenCodeServerError(
                    f"POST {path} returned {exc.response.status_code}"
                ) from exc

    # ------------------------------------------------------------------
    # The three things the harness actually needs
    # ------------------------------------------------------------------

    async def health(self) -> dict[str, t.Any]:
        """GET /global/health → ``{"healthy": true, "version": "..."}``."""
        return await self.get("/global/health")

    async def send(
        self,
        session_id: str,
        message: str,
        *,
        model: str | None = None,
        agent: str | None = None,
        tools: dict[str, bool] | None = None,
        system: str | None = None,
    ) -> None:
        """POST /session/:id/prompt_async — non-blocking message send.

        Returns immediately. Events arrive on :meth:`events`.

        ``model`` accepts either a ``"providerID/modelID"`` shorthand string
        or a raw ``{"providerID": ..., "modelID": ...}`` dict.
        """
        parts: list[dict[str, t.Any]] = [{"type": "text", "text": message}]
        body: dict[str, t.Any] = {"parts": parts}

        if model is not None:
            if isinstance(model, str) and "/" in model:
                provider_id, model_id = model.split("/", 1)
                body["model"] = {"providerID": provider_id, "modelID": model_id}
            else:
                body["model"] = model
        if agent is not None:
            body["agent"] = agent
        if tools is not None:
            body["tools"] = tools
        if system is not None:
            body["system"] = system

        async with self._http() as http:
            try:
                r = await http.post(f"/session/{session_id}/prompt_async", json=body)
                if r.status_code not in (200, 204):
                    r.raise_for_status()
            except httpx.TimeoutException as exc:
                raise OpenCodeTimeoutError("send timed out") from exc
            except httpx.HTTPStatusError as exc:
                raise OpenCodeServerError(f"send returned {exc.response.status_code}") from exc

    async def events(self, session_id: str) -> t.AsyncIterator[OpenCodeEvent]:
        """GET /global/event — SSE bus filtered to ``session_id``.

        Yields :class:`~opencode_harness.event.OpenCodeEvent` and terminates
        on ``session.idle`` or an error event for this session.
        """
        async with self._http() as http:
            async with http.stream("GET", "/global/event") as r:
                try:
                    r.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise OpenCodeServerError(
                        f"SSE /global/event returned {exc.response.status_code}"
                    ) from exc

                async for line in r.aiter_lines():
                    line = line.strip()

                    if not line.startswith("data:"):
                        continue

                    raw_data = line[len("data:") :].strip()
                    if not raw_data:
                        continue

                    try:
                        envelope = json.loads(raw_data)
                    except json.JSONDecodeError:
                        continue

                    # /global/event wraps each event: {"payload": {"type": ..., "properties": ...}}
                    payload = envelope.get("payload", {})
                    if not isinstance(payload, dict):
                        continue

                    event_type = payload.get("type", "")
                    props = payload.get("properties", {})
                    if not isinstance(props, dict):
                        props = {}

                    # Filter to this session (events without sessionID are global, pass through)
                    sid = props.get("sessionID")
                    if sid is not None and sid != session_id:
                        continue

                    if event_type == "message.part.updated":
                        part = props.get("part", {})
                        # Only yield text from assistant response parts.
                        # User message echoes have type="text" but no "time" field.
                        if part.get("type") == "text" and "time" in part:
                            text = part.get("text")
                            if text:
                                yield OpenCodeEvent(type="message.delta", text=text, raw=payload)

                    elif event_type == "session.idle":
                        yield OpenCodeEvent(type="message.completed", raw=payload)
                        return

                    elif event_type == "session.status":
                        status = props.get("status", {})
                        if isinstance(status, dict) and status.get("type") == "idle":
                            yield OpenCodeEvent(type="message.completed", raw=payload)
                            return

                    elif event_type == "session.error":
                        error = props.get("error", {})
                        msg = (
                            error.get("data", {}).get("message", "")
                            if isinstance(error, dict)
                            else str(error)
                        )
                        yield OpenCodeEvent(type="error", text=msg, raw=payload)
                        return
