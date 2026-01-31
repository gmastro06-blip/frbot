from __future__ import annotations

from typing import Protocol


class InputAdapter(Protocol):
    name: str

    def preflight(self) -> bool:
        """Returns True only if input is verified safe to use."""

    def click(self, x: int, y: int) -> None:
        """Perform an input action. Must only run after successful preflight."""
