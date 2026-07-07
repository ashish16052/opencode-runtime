class OpenCodeRuntimeError(Exception):
    """Base class for all opencode-runtime errors."""


class OpenCodeNotFoundError(OpenCodeRuntimeError):
    """Raised when the opencode binary cannot be found on PATH."""


class OpenCodeServerError(OpenCodeRuntimeError):
    """Raised when the opencode server fails to start or returns an error."""


class OpenCodeTimeoutError(OpenCodeRuntimeError):
    """Raised when a health check or request exceeds the allowed timeout."""
