from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.capture import Frame
from contracts.evidence import Roi


# Tipos de potions en Tibia
POTION_TYPES = {
    'health': {'name': 'Health Potion', 'color': (200, 50, 50)},
    'mana': {'name': 'Mana Potion', 'color': (50, 50, 200)},
    'strong_health': {'name': 'Strong Health Potion', 'color': (180, 40, 40)},
    'strong_mana': {'name': 'Strong Mana Potion', 'color': (40, 40, 180)},
    'great_health': {'name': 'Great Health Potion', 'color': (150, 30, 30)},
    'great_mana': {'name': 'Great Mana Potion', 'color': (30, 30, 150)},
}


@dataclass(frozen=True, slots=True)
class SupplySettings:
    """Configuration for auto-supply (potions refill).

    Features:
    - Auto-drink potions when HP/MP is low
    - Auto-buy potions from NPC shop
    - Track supply levels
    """
    # Enable supply features
    enable_potion_refill: bool = False
    enable_potion_buy: bool = False

    # HP/MP threshold to drink potion (0.0 - 1.0)
    hp_threshold: float = 0.5
    mp_threshold: float = 0.3

    # Key to drink potions
    health_potion_key: str = 'F1'
    mana_potion_key: str = 'F2'

    # Potion types
    health_potion_type: str = 'health'
    mana_potion_type: str = 'mana'

    # Buy settings
    buy_potion_key: str = 'F3'
    buy_npc_roi: str = 'trade_npc'
    buy_confirm_key: str = 'enter'

    # Cooldown between drinks (ms)
    drink_interval_ms: int = 1000

    # Minimum potions to trigger buy
    min_potions_to_buy: int = 5


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


def parse_hp_from_bar(frame: Frame, hp_bar_roi: Roi) -> Optional[float]:
    """Parse HP percentage from HP bar ROI.

    Returns:
        HP as float 0.0-1.0, or None if unreadable
    """
    crop = _crop_roi_rgb(frame, hp_bar_roi)
    if not crop:
        return None

    # Analyze green vs red ratio
    # HP bar is typically green when full, red when empty
    width = int(getattr(hp_bar_roi, 'width', 0) or 0)
    if width <= 0:
        return None

    # Sample first row for HP percentage
    green_count = 0
    red_count = 0

    for i in range(0, min(len(crop), width * 3), 3):
        r = crop[i]
        g = crop[i + 1]
        _ = crop[i + 2]

        # Greenish = high HP, Reddish = low HP
        if g > r and g > 100:
            green_count += 1
        elif r > g and r > 100:
            red_count += 1

    total = green_count + red_count
    if total == 0:
        return None

    return green_count / total


def parse_mp_from_bar(frame: Frame, mp_bar_roi: Roi) -> Optional[float]:
    """Parse MP percentage from MP bar ROI.

    Returns:
        MP as float 0.0-1.0, or None if unreadable
    """
    crop = _crop_roi_rgb(frame, mp_bar_roi)
    if not crop:
        return None

    width = int(getattr(mp_bar_roi, 'width', 0) or 0)
    if width <= 0:
        return None

    # Blue = MP present
    blue_count = 0
    total_count = 0

    for i in range(0, min(len(crop), width * 3), 3):
        b = crop[i + 2]
        if b > 100:
            blue_count += 1
        total_count += 1

    if total_count == 0:
        return None

    return blue_count / total_count


def should_drink_health_potion(
    hp_percent: Optional[float],
    settings: SupplySettings,
) -> bool:
    """Check if should drink health potion.

    Args:
        hp_percent: Current HP as 0.0-1.0, or None if unreadable
        settings: Supply configuration

    Returns:
        True if should drink
    """
    if hp_percent is None:
        return False
    return hp_percent < settings.hp_threshold


def should_drink_mana_potion(
    mp_percent: Optional[float],
    settings: SupplySettings,
) -> bool:
    """Check if should drink mana potion.

    Args:
        mp_percent: Current MP as 0.0-1.0, or None if unreadable
        settings: Supply configuration

    Returns:
        True if should drink
    """
    if mp_percent is None:
        return False
    return mp_percent < settings.mp_threshold


@dataclass(frozen=True, slots=True)
class SupplyState:
    """State tracking for auto-supply."""
    last_drink_ts_ms: int = 0
    drink_count: int = 0
    potions_in_inventory: int = 0


def auto_supply_tick(
    frame: Optional[Frame],
    hp_bar_roi: Optional[Roi],
    mp_bar_roi: Optional[Roi],
    inventory_text: str,
    settings: SupplySettings,
    now_ms: int,
    last_drink_ts_ms: Optional[int],
) -> tuple[bool, bool, bool, Optional[str], int | None]:
    """Execute auto-supply tick.

    Returns:
        (should_drink_hp: bool, should_drink_mp: bool, should_buy: bool,
         action_key: str | None, new_last_drink_ts: int | None)

    Priority:
        1. Drink health if HP < threshold
        2. Drink mana if MP < threshold
        3. Buy potions if low count
    """
    if not settings.enable_potion_refill:
        return (False, False, False, None, last_drink_ts_ms)

    # Check cooldown
    cooldown_ready = (
        last_drink_ts_ms is None or
        int(now_ms) >= (int(last_drink_ts_ms) + int(settings.drink_interval_ms))
    )

    if not cooldown_ready:
        return (False, False, False, None, last_drink_ts_ms)

    # Parse HP/MP
    hp_percent = None
    if frame is not None and hp_bar_roi is not None:
        hp_percent = parse_hp_from_bar(frame, hp_bar_roi)

    mp_percent = None
    if frame is not None and mp_bar_roi is not None:
        mp_percent = parse_mp_from_bar(frame, mp_bar_roi)

    # Check if should drink
    drink_hp = should_drink_health_potion(hp_percent, settings)
    drink_mp = should_drink_mana_potion(mp_percent, settings)

    # Priority: HP > MP
    if drink_hp:
        return (True, False, False, settings.health_potion_key, now_ms)

    if drink_mp:
        return (False, True, False, settings.mana_potion_key, now_ms)

    # Check if should buy potions
    should_buy = False
    if settings.enable_potion_buy:
        # TODO: Count potions from inventory OCR
        potion_count = _count_potions_in_inventory(inventory_text)
        if potion_count < settings.min_potions_to_buy:
            should_buy = True

    return (False, False, should_buy, None, last_drink_ts_ms)


def _count_potions_in_inventory(inventory_text: str) -> int:
    """Count potions in inventory text.

    Args:
        inventory_text: OCR text from inventory

    Returns:
        Number of potions detected
    """
    if not inventory_text:
        return 0

    text_lower = inventory_text.lower()
    count = 0

    # Match by the canonical potion name when possible (e.g. "Health Potion")
    for potion_key, meta in POTION_TYPES.items():
        name = str(meta.get('name', '') or '')
        if name:
            count += text_lower.count(name.lower())
        else:
            # Fallback to matching the key substring
            if potion_key in text_lower:
                count += 1

    return count
