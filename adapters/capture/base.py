from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Frame:
    """A minimal frame abstraction.

    No image/GUI dependencies in core.
    """

    width: int
    height: int
    monotonic_ts_ns: int


class CaptureAdapter(Protocol):
    name: str

    def preflight(self) -> bool:
        """Returns True only if capture is verified functional."""

    def grab(self) -> Frame:
        """Grab a frame. Must only be called after successful preflight."""
