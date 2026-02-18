from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.capture import Frame
from contracts.evidence import Roi


# All available Tibia rings with their effects
TIBIA_RINGS = {
    'power_ring': {'effect': '+6 Fist skills', 'category': 'combat'},
    'might_ring': {'effect': 'Increases combat power', 'category': 'combat'},
    'axe_ring': {'effect': '+4 Axe skills', 'category': 'combat'},
    'sword_ring': {'effect': '+4 Sword skills', 'category': 'combat'},
    'club_ring': {'effect': '+4 Club skills', 'category': 'combat'},
    'energy_ring': {'effect': 'Mana shield', 'category': 'defense'},
    'life_ring': {'effect': 'Faster regeneration', 'category': 'healing'},
    'ring_of_healing': {'effect': 'Faster regeneration', 'category': 'healing'},
    'time_ring': {'effect': 'Speed +30', 'category': 'movement'},
    'stealth_ring': {'effect': 'Invisible', 'category': 'stealth'},
    'death_ring': {'effect': '+1 Shielding, +5% death protection', 'category': 'defense'},
    'dwarven_ring': {'effect': 'Prevents drunkenness', 'category': 'utility'},
}


@dataclass(frozen=True, slots=True)
class RingSettings:
    """Configuration for auto-ring equip feature.

    Tibia rings provide buffs:
    - Power Ring: +6 Fist skills
    - Might Ring: Increases combat power
    - Energy Ring: Mana shield
    - Life Ring / Ring of Healing: Faster regeneration
    - Stealth Ring: Invisible
    - Time Ring: Speed +30
    - etc.

    Auto-ring tracks:
    - Ring slot status (equipped vs empty)
    - Auto-equip from inventory
    - Ring rotation based on situation
    """
    # ROI to detect ring slot status
    ring_slot_roi: str = 'ring_slot'

    # Key to equip/unequip rings
    equip_key: str = 'F11'

    # RGB color for equipped ring indicator
    equipped_rgb: tuple[int, int, int] = (255, 255, 255)
    color_tol: int = 20

    # Minimum ratio to consider ring equipped
    equipped_ratio_min: float = 0.3

    # Cooldown between ring switches (ms)
    ring_switch_interval_ms: int = 5000

    # Default rings for different situations
    combat_ring: str = 'power_ring'
    defense_ring: str = 'energy_ring'
    healing_ring: str = 'life_ring'
    default_ring: str = 'power_ring'


def _crop_roi_rgb(frame: Frame, roi: Roi) -> bytes:
    """Crop RGB bytes from frame using ROI."""
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


def ring_equipped_match_ratio(frame: Frame, roi: Roi, settings: RingSettings) -> float:
    """Calculate ratio of pixels matching equipped ring color."""
    crop = _crop_roi_rgb(frame, roi)
    if not crop:
        return 0.0

    tr = int(settings.equipped_rgb[0])
    tg = int(settings.equipped_rgb[1])
    tb = int(settings.equipped_rgb[2])
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


def is_ring_equipped(frame: Frame, roi: Roi, settings: RingSettings) -> tuple[bool, float]:
    """Check if ring is equipped in slot.

    Returns:
        (equipped: bool, match_ratio: float)
    """
    ratio = float(ring_equipped_match_ratio(frame, roi, settings))
    min_ratio = max(0.0, min(1.0, float(settings.equipped_ratio_min)))
    return (bool(ratio >= min_ratio), float(ratio))


def should_equip_ring(*, now_ms: int, last_ring_ts_ms: Optional[int], ring_switch_interval_ms: int) -> bool:
    """Check if enough time has passed to switch rings.

    Returns:
        True if cooldown is ready
    """
    if last_ring_ts_ms is None:
        return True
    return int(now_ms) >= (int(last_ring_ts_ms) + max(0, int(ring_switch_interval_ms)))


def check_ring_in_inventory(inventory_text: str, ring_name: str) -> bool:
    """Check if ring is in inventory.

    Args:
        inventory_text: OCR text from inventory
        ring_name: Ring name to look for (e.g., 'power ring', 'stone skin')

    Returns:
        True if ring detected in inventory
    """
    if not inventory_text:
        return False

    text_lower = inventory_text.lower()
    ring_lower = ring_name.lower()

    return ring_lower in text_lower


def get_ring_to_equip(
    inventory_text: str,
    settings: RingSettings,
    current_ring: Optional[str],
) -> Optional[str]:
    """Determine which ring to equip.

    Args:
        inventory_text: OCR text from inventory
        settings: Ring configuration
        current_ring: Currently equipped ring (None if empty)

    Returns:
        Ring name to equip, or None if no change needed
    """
    # Normalize inventory text for matching (OCR often converts " " to "_")
    normalized_inventory = inventory_text.lower().replace(' ', '_').replace('-', '_')

    # If no ring currently equipped, equip default
    if current_ring is None:
        # Check with normalized match
        default_normalized = settings.default_ring.lower().replace(' ', '_').replace('-', '_')
        if default_normalized in normalized_inventory:
            return settings.default_ring

    # Basic rotation heuristic (deterministic, minimal): prefer combat, then
    # defense, then healing, then default when present in inventory. This is
    # intentionally conservative: only returns a ring when it is present in the
    # OCR inventory and different from `current_ring`.
    candidates = [
        settings.combat_ring,
        settings.defense_ring,
        settings.healing_ring,
        settings.default_ring,
    ]

    for candidate in candidates:
        if not candidate:
            continue
        cand_norm = candidate.lower().replace(' ', '_').replace('-', '_')
        if cand_norm in normalized_inventory and candidate != current_ring:
            return candidate

    return None


@dataclass(frozen=True, slots=True)
class RingState:
    """State tracking for auto-ring feature."""
    equipped_ring: Optional[str] = None
    last_switch_ts_ms: int = 0
    switch_count: int = 0


def auto_ring_tick(
    frame: Optional[Frame],
    ring_roi: Optional[Roi],
    inventory_text: str,
    settings: RingSettings,
    now_ms: int,
    last_ring_ts_ms: Optional[int],
    current_ring: Optional[str],
) -> tuple[bool, Optional[str], int | None, float]:
    """Execute auto-ring tick.

    Returns:
        (should_equip: bool, ring_to_equip: str | None, new_last_ts: int | None, equipped_ratio: float)

    Logic:
        1. Check cooldown
        2. Detect current ring slot status
        3. Determine if new ring needed
        4. Return action
    """
    equipped_ratio = 0.0
    is_equipped = False

    # Check if ring is currently equipped
    if frame is not None and ring_roi is not None:
        is_equipped, equipped_ratio = is_ring_equipped(frame, ring_roi, settings)

    # Check cooldown
    if not should_equip_ring(now_ms=now_ms, last_ring_ts_ms=last_ring_ts_ms, ring_switch_interval_ms=settings.ring_switch_interval_ms):
        return (False, None, last_ring_ts_ms, equipped_ratio)

    # Determine if we need to change ring
    new_ring = get_ring_to_equip(inventory_text, settings, current_ring)

    if new_ring is not None and new_ring != current_ring:
        # Need to equip new ring
        return (True, new_ring, now_ms, equipped_ratio)

    # No action needed
    return (False, None, last_ring_ts_ms, equipped_ratio)
