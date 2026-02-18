from __future__ import annotations

from dataclasses import dataclass

from contracts.capture import Frame
from contracts.evidence import Roi


@dataclass(frozen=True, slots=True)
class HungerSettings:
    roi_name: str = 'hunger_status'
    eat_key: str = 'F9'
    hungry_rgb: tuple[int, int, int] = (255, 170, 0)
    color_tol: int = 28
    match_ratio_min: float = 0.08
    eat_interval_ms: int = 1200


def parse_rgb(raw: str, default_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    s = str(raw or '').strip()
    parts = [p.strip() for p in s.split(',') if p.strip()]
    if len(parts) != 3:
        return default_rgb
    try:
        r = max(0, min(255, int(parts[0])))
        g = max(0, min(255, int(parts[1])))
        b = max(0, min(255, int(parts[2])))
        return (int(r), int(g), int(b))
    except Exception:
        return default_rgb


def _crop_roi_rgb(frame: Frame, roi: Roi) -> bytes:
    rgb = bytes(getattr(frame, 'rgb', b'') or b'')
    fw = int(getattr(frame, 'width', 0) or 0)
    fh = int(getattr(frame, 'height', 0) or 0)
    if not rgb or fw <= 0 or fh <= 0:
        return b''
    if len(rgb) != (fw * fh * 3):
        return b''

    x0 = int(getattr(roi, 'x', 0) or 0)
    y0 = int(getattr(roi, 'y', 0) or 0)
    rw = int(getattr(roi, 'width', 0) or 0)
    rh = int(getattr(roi, 'height', 0) or 0)
    if x0 < 0 or y0 < 0 or rw <= 0 or rh <= 0:
        return b''
    if (x0 + rw) > fw or (y0 + rh) > fh:
        return b''

    out = bytearray()
    for yy in range(y0, y0 + rh):
        i0 = (yy * fw + x0) * 3
        i1 = i0 + (rw * 3)
        out.extend(rgb[i0:i1])
    return bytes(out)


def hunger_match_ratio(frame: Frame, roi: Roi, settings: HungerSettings) -> float:
    crop = _crop_roi_rgb(frame, roi)
    if not crop:
        return 0.0

    tr = int(settings.hungry_rgb[0])
    tg = int(settings.hungry_rgb[1])
    tb = int(settings.hungry_rgb[2])
    tol = max(0, min(255, int(settings.color_tol)))

    n = int(len(crop) // 3)
    if n <= 0:
        return 0.0

    matches = 0
    for i in range(0, len(crop), 3):
        r = int(crop[i + 0])
        g = int(crop[i + 1])
        b = int(crop[i + 2])
        if abs(r - tr) <= tol and abs(g - tg) <= tol and abs(b - tb) <= tol:
            matches += 1
    return float(matches) / float(n)


def is_hungry(frame: Frame, roi: Roi, settings: HungerSettings) -> tuple[bool, float]:
    ratio = float(hunger_match_ratio(frame, roi, settings))
    min_ratio = max(0.0, min(1.0, float(settings.match_ratio_min)))
    return (bool(ratio >= min_ratio), float(ratio))


def should_press_eat(*, hungry: bool, now_ms: int, last_eat_ts_ms: int | None, eat_interval_ms: int) -> bool:
    if not bool(hungry):
        return False
    if last_eat_ts_ms is None:
        return True
    return int(now_ms) >= (int(last_eat_ts_ms) + max(0, int(eat_interval_ms)))


def auto_eat_tick(
    frame: Frame,
    roi: Roi,
    settings: HungerSettings,
    now_ms: int,
    last_eat_ts_ms: int | None,
) -> tuple[bool, int | None, float]:
    """Execute auto-eat tick for integration with healing/combat.

    Returns:
        (ate: bool, new_last_eat_ts_ms: int | None, hunger_ratio: float)

    Usage:
        ate, new_ts, ratio = auto_eat_tick(frame, roi, settings, now_ms, last_eat_ts_ms)
    """
    hungry, ratio = is_hungry(frame, roi, settings)

    if should_press_eat(
        hungry=hungry,
        now_ms=now_ms,
        last_eat_ts_ms=last_eat_ts_ms,
        eat_interval_ms=settings.eat_interval_ms,
    ):
        return (True, now_ms, ratio)

    return (False, last_eat_ts_ms, ratio)
