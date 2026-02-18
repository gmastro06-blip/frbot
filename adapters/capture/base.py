from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Frame:
    """A minimal frame abstraction.

    Attributes:
        width: Frame width in pixels (> 0)
        height: Frame height in pixels (> 0)
        monotonic_ts_ns: Monotonic timestamp in nanoseconds (for timing)
        rgb: Raw RGB pixel data (optional, for validation)
    """

    width: int
    height: int
    monotonic_ts_ns: int
    rgb: bytes | None = None

    def validate(self) -> list[str]:
        """Validate frame dimensions and return list of errors."""
        errors = []
        if self.width <= 0:
            errors.append(f"invalid width: {self.width}")
        if self.height <= 0:
            errors.append(f"invalid height: {self.height}")
        if self.monotonic_ts_ns <= 0:
            errors.append(f"invalid timestamp: {self.monotonic_ts_ns}")
        # Validate RGB if present
        if self.rgb is not None:
            expected_len = self.width * self.height * 3
            if len(self.rgb) != expected_len:
                errors.append(f"invalid rgb length: {len(self.rgb)} != {expected_len}")
        return errors


@dataclass(frozen=True, slots=True)
class CaptureError:
    """Capture error with context."""

    reason: str
    details: str = ""
    retryable: bool = True


# Default timeout for grab() in milliseconds
DEFAULT_GRAB_TIMEOUT_MS = 1000


@runtime_checkable
class CaptureAdapter(Protocol):
    """Protocol for capture backends.

    All implementations must:
    1. Define a `name` class attribute
    2. Implement preflight() -> bool
    3. Implement grab() -> Frame
    4. Implement verify() -> VerificationResult (optional but recommended)
    """

    name: str

    def preflight(self) -> bool:
        """Returns True only if capture is verified functional.

        Must be called before grab(). If preflight fails, grab() behavior is undefined.
        """

    def grab(self) -> Frame:
        """Grab a frame.

        Must only be called after successful preflight().

        Raises:
            CaptureError: On capture failure (with retryable flag)
            TimeoutError: If grab exceeds timeout

        Returns:
            Frame: Valid frame with positive dimensions
        """

    def verify(self) -> VerificationResult:
        """Verify capture is working. Returns verification result."""
        ...


# Verification result (avoid circular import)
@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Result of a verification operation."""

    ok: bool
    reason: str = ""
    details: dict | None = None
