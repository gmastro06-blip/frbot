from __future__ import annotations

import pytest

from adapters.capture.obs_source_real import ObsSourceRealCapture
from contracts.evidence import Roi
from contracts.errors import PreflightFailed


def _mk_rois() -> dict[str, Roi]:
    # Minimal required ROI set for semantic validation.
    return {
        'minimap': Roi(name='minimap', x=0, y=0, width=2, height=2),
        'battle_list': Roi(name='battle_list', x=0, y=0, width=2, height=2),
        'hp_mp': Roi(name='hp_mp', x=0, y=0, width=2, height=2),
        'target_frame': Roi(name='target_frame', x=0, y=0, width=2, height=2),
    }


def test_obs_source_capture_ok() -> None:
    w, h = 4, 4

    def provider(_name: str, _w: int, _h: int) -> tuple[bytes, int, int]:
        # Non-black, non-constant RGB with some variance.
        rgb = bytearray(w * h * 3)
        for i in range(0, len(rgb), 3):
            v = (i // 3) % 255
            rgb[i] = v
            rgb[i + 1] = (v * 2) % 255
            rgb[i + 2] = (v * 3) % 255
        return bytes(rgb), w, h

    cap = ObsSourceRealCapture(
        obs_source_name='TibiaSource',
        expected_width=w,
        expected_height=h,
        rois=_mk_rois(),
        minimap_roi_name='minimap',
        provider=provider,
    )

    assert cap.verify().ok
    frame = cap.grab()
    assert (frame.width, frame.height) == (w, h)
    assert frame.minimap_detected is True


def test_obs_source_black_frame_abort() -> None:
    w, h = 4, 4

    def provider(_name: str, _w: int, _h: int) -> tuple[bytes, int, int]:
        return bytes([0] * (w * h * 3)), w, h

    cap = ObsSourceRealCapture(
        obs_source_name='TibiaSource',
        expected_width=w,
        expected_height=h,
        rois=_mk_rois(),
        minimap_roi_name='minimap',
        provider=provider,
    )

    with pytest.raises(PreflightFailed) as ei:
        cap.grab()
    assert str(ei.value) == 'obs_capture_invalid_content'
    d = getattr(ei.value, 'details', {})
    assert d.get('error') == 'frame_black_or_low_variance'
    assert d.get('all_zero') is True


def test_obs_source_wrong_resolution_abort() -> None:
    w, h = 4, 4

    def provider(_name: str, _w: int, _h: int) -> tuple[bytes, int, int]:
        ww, hh = 8, 8
        rgb = bytearray(ww * hh * 3)
        rgb[0] = 255
        return bytes(rgb), ww, hh

    cap = ObsSourceRealCapture(
        obs_source_name='TibiaSource',
        expected_width=w,
        expected_height=h,
        rois=_mk_rois(),
        minimap_roi_name='minimap',
        provider=provider,
    )

    with pytest.raises(PreflightFailed) as ei:
        cap.grab()
    assert str(ei.value) == 'obs_capture_invalid_content'
    d = getattr(ei.value, 'details', {})
    assert d.get('error') == 'wrong_resolution'
