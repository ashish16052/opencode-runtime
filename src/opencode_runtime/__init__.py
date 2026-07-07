"""
opencode-harness: a thin OpenCode harness for backend applications.
"""

__version__ = "0.3.0"

from .event import OpenCodeEvent
from .harness import OpenCodeHarness
from .response import OpenCodeResponse
from .session import OpenCodeSession

__all__ = [
    "OpenCodeHarness",
    "OpenCodeSession",
    "OpenCodeEvent",
    "OpenCodeResponse",
]
