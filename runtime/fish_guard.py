from __future__ import annotations

from dataclasses import dataclass

from contracts.capture import Frame
from contracts.evidence import Roi


@dataclass(frozen=True, slots=True)
class FishSettings:
    """Configuration for auto-fish feature.

    Based on Tibia mechanics:
    - Requires Fishing Rod + Worms in inventory
    - Click on water tile to fish
    - ~50% max success rate at skill 77+
    - Fish respawn: ~36 minutes
    """
    roi_name: str = 'fishing_indicator'
    fish_key: str = 'F10'

    # RGB color that indicates fish is biting (green pulse)
    bite_rgb: tuple[int, int, int] = (0, 255, 0)
    color_tol: int = 30
    match_ratio_min: float = 0.05

    # Cooldown between fishing cast attempts (Tibia natural cooldown)
    fish_interval_ms: int = 2000

    # Maximum time to wait for bite (ms) - Tibia fishing is slow
    bite_timeout_ms: int = 5000

    # Require worms in inventory for fishing
    require_bait: bool = True
    bait_item: str = 'worm'

    # Minimum fishing skill level
    min_skill: int = 0


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


def fish_bite_match_ratio(frame: Frame, roi: Roi, settings: FishSettings) -> float:
    """Calculate ratio of pixels matching fish bite color."""
    crop = _crop_roi_rgb(frame, roi)
    if not crop:
        return 0.0

    tr = int(settings.bite_rgb[0])
    tg = int(settings.bite_rgb[1])
    tb = int(settings.bite_rgb[2])
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


def is_fish_biting(frame: Frame, roi: Roi, settings: FishSettings) -> tuple[bool, float]:
    """Check if fish is biting (green indicator present).

    Returns:
        (is_biting: bool, match_ratio: float)
    """
    ratio = float(fish_bite_match_ratio(frame, roi, settings))
    min_ratio = max(0.0, min(1.0, float(settings.match_ratio_min)))
    return (bool(ratio >= min_ratio), float(ratio))


def should_fish(*, now_ms: int, last_fish_ts_ms: int | None, fish_interval_ms: int) -> bool:
    """Check if we should attempt to fish based on cooldown.

    Returns:
        True if enough time has passed since last fish attempt
    """
    if last_fish_ts_ms is None:
        return True
    return int(now_ms) >= (int(last_fish_ts_ms) + max(0, int(fish_interval_ms)))


def auto_fish_tick(
    frame: Frame,
    roi: Roi,
    settings: FishSettings,
    now_ms: int,
    last_fish_ts_ms: int | None,
) -> tuple[bool, int | None, float]:
    """Execute auto-fish tick for integration with combat/healing.

    Returns:
        (fished: bool, new_last_fish_ts_ms: int | None, bite_ratio: float)

    Logic:
        1. Check cooldown - if not ready, return (False, last_ts, ratio)
        2. Check for fish bite indicator
        3. If biting, return (True, now_ms, ratio) to trigger fish action
        4. If not biting, return (False, last_ts, ratio)
    """
    # Check cooldown first
    if not should_fish(now_ms=now_ms, last_fish_ts_ms=last_fish_ts_ms, fish_interval_ms=settings.fish_interval_ms):
        bite_ratio = 0.0
        # Still check for bite even during cooldown
        if roi is not None:
            _, bite_ratio = is_fish_biting(frame, roi, settings)
        return (False, last_fish_ts_ms, bite_ratio)

    # Check for fish bite
    biting, ratio = is_fish_biting(frame, roi, settings)

    if biting:
        # Fish now!
        return (True, now_ms, ratio)

    return (False, last_fish_ts_ms, ratio)


def calculate_fish_chance(fishing_skill: int) -> float:
    """Calculate fishing success chance based on skill level.

    Tibia mechanics:
    - Max 50% at skill 77+
    - Formula: min(0.5, skill / 154)
    """
    if fishing_skill >= 77:
        return 0.5
    return min(0.5, fishing_skill / 154.0)


def check_bait_in_inventory(inventory_text: str, bait_item: str = 'worm') -> bool:
    """Check if bait item is in inventory.

    Args:
        inventory_text: OCR text from inventory
        bait_item: Item name to look for (default: worm)

    Returns:
        True if bait is detected
    """
    if not inventory_text:
        return False

    text_lower = inventory_text.lower()
    bait_lower = bait_item.lower()

    return bait_lower in text_lower
