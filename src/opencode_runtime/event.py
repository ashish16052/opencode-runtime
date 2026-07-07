from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpenCodeEvent:
    """A normalized event from the OpenCode server SSE stream.

    Attributes:
        type:  Event type string, e.g. "message.delta", "message.completed",
               "session.error". Mirrors the OpenCode bus event types.
        text:  Text content for message.delta events; None for other types.
        raw:   The raw event payload from the server. Use this escape hatch
               when you need fields beyond type and text.
    """

    type: str
    text: str | None = None
    raw: Any = None

    def to_sse(self) -> str:
        """Format as a Server-Sent Events string suitable for HTTP streaming."""
        data = self.text or ""
        return f"event: {self.type}\ndata: {data}\n\n"
