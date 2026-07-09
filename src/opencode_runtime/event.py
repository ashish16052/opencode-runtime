from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OpenCodeEvent:
    """A normalized event from the OpenCode server SSE stream.

    Attributes:
        type:  Event type string, e.g. "message.part.delta", "message.part.updated",
               "session.error". Mirrors the OpenCode bus event types.
        text:  Text content for message.part.delta events (when properties.field == "text")
               and message.part.updated events (when part.type == "text"); None otherwise.
        raw:   The raw event payload from the server. Use this escape hatch
               when you need fields beyond type and text.
    """

    type: str
    text: str | None = None
    raw: Any = None
