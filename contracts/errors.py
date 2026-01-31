from __future__ import annotations


class ContractViolation(RuntimeError):
    """Raised when an invariant is violated by input/state."""


class PreflightFailed(RuntimeError):
    """Raised when runtime requirements are not met."""


class FatalRuntimeError(RuntimeError):
    """Raised when the runtime must abort immediately."""
