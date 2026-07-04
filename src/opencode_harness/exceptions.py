class OpenCodeHarnessError(Exception):
    """Base class for all opencode-harness errors."""


class OpenCodeNotFoundError(OpenCodeHarnessError):
    """Raised when the opencode binary cannot be found on PATH."""


class OpenCodeServerError(OpenCodeHarnessError):
    """Raised when the opencode server fails to start or returns an error."""


class OpenCodeTimeoutError(OpenCodeHarnessError):
    """Raised when a health check or request exceeds the allowed timeout."""
