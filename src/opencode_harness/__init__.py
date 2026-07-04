"""
opencode-harness: a thin OpenCode harness for backend applications.
"""

__version__ = "0.1.0"

from .event import OpenCodeEvent
from .response import OpenCodeResponse

__all__ = [
    "OpenCodeEvent",
    "OpenCodeResponse",
]
