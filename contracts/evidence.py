from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional


@dataclass(frozen=True, slots=True)
class Roi:
    """Region of interest in screen pixel coordinates."""

    name: str
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("roi width/height must be > 0")
        if self.x < 0 or self.y < 0:
            raise ValueError("roi x/y must be >= 0")


@dataclass(frozen=True, slots=True)
class EvidenceConfig:
    """Defines ROIs that runtime can use for objective verification.

    Notes:
    - ROIs are interpreted against the full captured frame.
    - Runtime MUST abort if an intent references an unknown ROI.
    """

    rois: Mapping[str, Roi]

    def get(self, name: str) -> Optional[Roi]:
        return self.rois.get(name)


@dataclass(frozen=True, slots=True)
class EvidenceExpectation:
    """Post-condition that must be observed after emitting input."""

    # ROIs that must change (digest delta) after input.
    roi_must_change: tuple[str, ...] = ()

    # Semantic post-conditions derived from Observation (runtime evidence).
    # If a field is set here but the runtime cannot extract it, runtime MUST abort.
    hp_percent_increase_min: Optional[float] = None
    require_has_target: Optional[bool] = None
    require_loot_available: Optional[bool] = None
    require_trade_open: Optional[bool] = None
    require_depot_open: Optional[bool] = None
    position_must_change: bool = False

    def iter_rois(self) -> Iterable[str]:
        return self.roi_must_change
