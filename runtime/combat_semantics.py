from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import os

from contracts.capture import Frame
from contracts.evidence import Roi
from runtime.healing_semantics import _crop_rgb, _clamp01


@dataclass(frozen=True, slots=True)
class TargetHpRead:
    value: float


def read_target_hp_percent(frame: Frame, roi: Roi) -> Optional[TargetHpRead]:
    """Read target HP percent from a mock-style red bar ROI."""

    rgb = _crop_rgb(frame, roi)
    if not rgb:
        return None

    total_px = int(roi.width) * int(roi.height)
    if total_px <= 0:
        return None

    try:
        red_min = int(os.environ.get('FRBOT_TARGET_HP_RED_MIN', '80') or '80')
    except Exception:
        red_min = 80
    try:
        dom_delta = int(os.environ.get('FRBOT_TARGET_HP_RED_DOM_DELTA', '25') or '25')
    except Exception:
        dom_delta = 25

    filled = 0
    for i in range(0, len(rgb) - 2, 3):
        r = rgb[i]
        g = rgb[i + 1]
        b = rgb[i + 2]
        # Prefer dominance over absolute RGB values to support multiple UI themes.
        if int(r) >= int(red_min) and int(r) >= (int(g) + int(dom_delta)) and int(r) >= (int(b) + int(dom_delta)):
            filled += 1

    return TargetHpRead(value=_clamp01(filled / float(total_px)))


def detect_damage_feedback(frame: Frame, roi: Roi, *, marker_rgb: tuple[int, int, int], tol: int) -> Optional[bool]:
    """Damage feedback marker detector.

    Returns True/False if ROI readable, else None.
    """

    rgb = _crop_rgb(frame, roi)
    if not rgb:
        return None

    mr, mg, mb = (int(marker_rgb[0]), int(marker_rgb[1]), int(marker_rgb[2]))
    t = int(max(0, min(255, tol)))

    for i in range(0, len(rgb) - 2, 3):
        r = int(rgb[i])
        g = int(rgb[i + 1])
        b = int(rgb[i + 2])
        if abs(r - mr) <= t and abs(g - mg) <= t and abs(b - mb) <= t:
            return True
    return False
