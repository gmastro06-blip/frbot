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


def validate_prod_emergency_real_rois_in_bounds(*, rois: dict[str, Roi], frame: Frame) -> None:
    """Validate the strict prod_emergency REAL ROI contract.

    - Requires exactly the fixed ROI names (already enforced by config_loader, but rechecked here)
    - Requires each ROI to be within the captured frame bounds
    """

    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    if profile != 'prod_emergency':
        return

    expected = set(_PROD_EMERGENCY_REQUIRED_ROIS)
    if set(rois.keys()) != expected:
        raise PreflightFailed('config_invalid_schema')

    fw = int(getattr(frame, 'width', 0) or 0)
    fh = int(getattr(frame, 'height', 0) or 0)
    if fw <= 0 or fh <= 0:
        raise PreflightFailed('capture_invalid')

    for name in _PROD_EMERGENCY_REQUIRED_ROIS:
        roi = rois.get(name)
        if roi is None:
            raise PreflightFailed('config_invalid_schema')
        if int(roi.width) <= 0 or int(roi.height) <= 0:
            raise PreflightFailed('config_invalid_schema')
        if int(roi.x) < 0 or int(roi.y) < 0:
            raise PreflightFailed('config_invalid_schema')
        if (int(roi.x) + int(roi.width)) > fw or (int(roi.y) + int(roi.height)) > fh:
            raise PreflightFailed('config_invalid_schema')
