from __future__ import annotations

import os

from contracts.capture import Frame
from contracts.evidence import Roi
from contracts.errors import PreflightFailed


_PROD_EMERGENCY_REQUIRED_ROIS: tuple[str, ...] = (
    'minimap',
    'battle_list',
    'hp_mp',
    'target_frame',
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _allowed_prod_emergency_extra_rois() -> set[str]:
    # Keep this set explicit and auditable.
    return {
        'target_hp_bar',
        'combat_cooldown',
        'combat_feedback',
        'chat_loot_area',
        'inventory_text',
        'loot_corpse',
    }


def _allowed_prod_full_extra_rois() -> set[str]:
    # Keep this set explicit and auditable.
    return {
        'target_hp_bar',
        'combat_cooldown',
        'combat_feedback',
        'chat_loot_area',
        'inventory_text',
        'loot_corpse',
        'depot_container',
        'trade_inventory',
        'trade_npc',
        'trade_action',
    }


def validate_prod_emergency_real_rois_in_bounds(*, rois: dict[str, Roi], frame: Frame) -> None:
    """Validate the strict PROD profile REAL ROI contract.

    Applies to:
    - prod_emergency
    - prod_full
    """

    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    if profile not in {'prod_emergency', 'prod_full'}:
        return

    expected = set(_PROD_EMERGENCY_REQUIRED_ROIS)

    keys = set(rois.keys())
    if not expected.issubset(keys):
        raise PreflightFailed('config_invalid_schema')

    allowed_extra = _allowed_prod_emergency_extra_rois() if profile == 'prod_emergency' else _allowed_prod_full_extra_rois()
    allowed = set(expected) | set(allowed_extra)

    if not keys.issubset(allowed):
        raise PreflightFailed('config_invalid_schema')

    fw = int(getattr(frame, 'width', 0) or 0)
    fh = int(getattr(frame, 'height', 0) or 0)
    if fw <= 0 or fh <= 0:
        raise PreflightFailed('capture_invalid')

    for name, roi in rois.items():
        if roi is None:
            raise PreflightFailed('config_invalid_schema')
        if int(roi.width) <= 0 or int(roi.height) <= 0:
            raise PreflightFailed('config_invalid_schema')
        if int(roi.x) < 0 or int(roi.y) < 0:
            raise PreflightFailed('config_invalid_schema')
        if (int(roi.x) + int(roi.width)) > fw or (int(roi.y) + int(roi.height)) > fh:
            raise PreflightFailed('config_invalid_schema')
