from __future__ import annotations

import os

from adapters.capture.obs_source_real import ObsSourceRealCapture
from contracts.capture import CaptureStatus
from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeContext, RuntimeState
from runtime.config_loader import load_rois
from runtime.minimap_semantics import detect_player_marker, marker_config_from_env


def _is_black_frame(rgb: bytes) -> bool:
    if not rgb:
        return True
    return all(int(b) == 0 for b in rgb)


def run_capture_only(ctx: RuntimeContext) -> ObsSourceRealCapture:
    """Route-recorder preflight that validates capture only (no input/foreground guards)."""
    ctx.status.state = RuntimeState.PREFLIGHT
    ctx.status.reason = ''

    os.environ['FRBOT_CAPTURE_SOURCE'] = 'obs_source'

    src = str(os.environ.get('FRBOT_OBS_SOURCE_NAME', '') or '').strip()
    if not src:
        raise PreflightFailed('obs_source_not_found')

    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)

    minimap_roi = ctx.rois.get(ctx.config.minimap_roi)
    if minimap_roi is None:
        raise PreflightFailed('minimap_not_detected')

    if loaded.frame_width is None or loaded.frame_height is None:
        raise PreflightFailed('config_invalid_schema')

    capture = ObsSourceRealCapture(
        obs_source_name=str(src),
        expected_width=int(loaded.frame_width),
        expected_height=int(loaded.frame_height),
        rois=ctx.rois,
        minimap_roi_name=str(ctx.config.minimap_roi),
    )

    cap_v = capture.verify()
    ctx.capture = CaptureStatus(backend=capture.name, verified=bool(cap_v.ok))
    if not cap_v.ok:
        raise PreflightFailed(cap_v.reason or 'capture not verified')

    frame = capture.grab()

    if int(frame.width) <= 0 or int(frame.height) <= 0:
        raise PreflightFailed('capture_black_or_unavailable')
    if len(frame.rgb) != int(frame.width) * int(frame.height) * 3:
        raise PreflightFailed('capture_black_or_unavailable')
    if _is_black_frame(frame.rgb):
        raise PreflightFailed('capture_black_or_unavailable')

    x0 = int(minimap_roi.x)
    y0 = int(minimap_roi.y)
    x1 = int(minimap_roi.x) + int(minimap_roi.width)
    y1 = int(minimap_roi.y) + int(minimap_roi.height)
    if x0 < 0 or y0 < 0 or x1 > int(frame.width) or y1 > int(frame.height):
        raise PreflightFailed('minimap_roi_out_of_bounds')

    if not bool(frame.minimap_detected) or len(frame.minimap_rgb) <= 0:
        raise PreflightFailed('minimap_not_detected')

    cfg = marker_config_from_env(
        str(ctx.config.player_marker_rgb),
        str(ctx.config.player_marker_tol),
        str(ctx.config.player_marker_min_pixels),
        str(ctx.config.player_marker_max_pixels),
        str(ctx.config.player_marker_min_fill_ratio),
        str(ctx.config.player_marker_max_aspect_ratio),
    )
    det = detect_player_marker(frame, cfg)
    if det is None:
        ctx.status.reason = 'minimap_player_not_found'

    ctx.status.state = RuntimeState.READY
    return capture
