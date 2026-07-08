from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OpenCodeResponse:
    """Collected response from a completed ask() call.

    Attributes:
        text:  Full assistant text, concatenated from all message.part.delta events.
        raw:   List of raw event objects received during the session, in order.
               Use this as an escape hatch when you need parts beyond plain text.
    """

    text: str
    raw: list[Any] = field(default_factory=list)
