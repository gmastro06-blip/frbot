from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from contracts.errors import PreflightFailed
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_enabled, dump_pair
from diagnostics.jsonlog import log as log_json
from diagnostics.last_frames import clear, record_after, record_before
from diagnostics.logger import configure_logger

from runtime.looting_basic_preflight import looting_basic_preflight
from runtime.inventory_semantics import diff_inventory, is_loot_success, read_inventory_pair_binary


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    s = str(raw).strip()
    if s == '':
        return None
    try:
        return int(s, 0)
    except Exception:
        return None


def _frames_dir() -> Path:
    raw = (os.environ.get('FRBOT_REAL_FRAMES_DIR', '') or '').strip()
    if raw:
        return Path(raw)
    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    if profile == 'prod_emergency':
        return Path('diagnostics') / 'frames_emergency'
    return Path('diagnostics') / 'frames'


def _read_bool_roi(frame: Any, roi: Any) -> bool | None:
    if frame is None or not getattr(frame, 'rgb', b''):
        return None
    try:
        w = int(getattr(frame, 'width', 0) or 0)
        h = int(getattr(frame, 'height', 0) or 0)
        if w <= 0 or h <= 0:
            return None
        if roi is None:
            return None
        x = int(getattr(roi, 'x', 0) or 0)
        y = int(getattr(roi, 'y', 0) or 0)
        rw = int(getattr(roi, 'width', 0) or 0)
        rh = int(getattr(roi, 'height', 0) or 0)
        if x < 0 or y < 0 or rw <= 0 or rh <= 0:
            return None
        if (x + rw) > w or (y + rh) > h:
            return None

        row_stride = w * 3
        out_row_stride = rw * 3
        src = frame.rgb
        for row in range(int(rh)):
            start = ((y + row) * row_stride) + (x * 3)
            end = start + out_row_stride
            for b in src[start:end]:
                if int(b) != 0:
                    return True
        return False
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='REAL quick-loot validation (Shift+RMB): 1 action, BEFORE/AFTER evidence')
    ap.add_argument('--gesture', default=None, choices=['shift_rmb', 'alt_q'], help='Quick-loot gesture to validate (default from FRBOT_TIBIA_LOOT_GESTURE or shift_rmb)')
    ap.add_argument('--x', type=int, default=None, help='ClickX (frame coords). Overrides FRBOT_LOOTING_BASIC_LOOT_X')
    ap.add_argument('--y', type=int, default=None, help='ClickY (frame coords). Overrides FRBOT_LOOTING_BASIC_LOOT_Y')
    ap.add_argument('--coord-space', default=None, choices=['frame', 'screen'], help='Coordinate space for click (default from env; recommended frame)')
    ap.add_argument('--loot-panel-roi', default=None, help='Optional ROI name to treat as loot panel/list evidence (default FRBOT_LOOT_PANEL_OPEN_ROI or loot_container_open)')
    args = ap.parse_args(argv)

    logger = configure_logger()
    frames_dir = _frames_dir()
    frames_dir.mkdir(parents=True, exist_ok=True)

    clear('quick_loot_validation')

    if sys.platform != 'win32':
        write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
        return 1

    gesture = (
        str(args.gesture)
        if args.gesture is not None
        else str(os.environ.get('FRBOT_TIBIA_LOOT_GESTURE', 'shift_rmb') or 'shift_rmb')
    ).strip().lower()
    if gesture not in {'shift_rmb', 'alt_q'}:
        write_fatal('invalid_precondition', details={'name': 'gesture', 'expected': ['shift_rmb', 'alt_q'], 'got': str(gesture)})
        return 1

    # Force the gesture for this validation tool.
    os.environ['FRBOT_TIBIA_LOOT_GESTURE'] = str(gesture)

    # Ensure runner-style validation label if called indirectly.
    os.environ['FRBOT_VALIDATE_QUICK_LOOT'] = '1'

    if args.coord_space:
        os.environ['FRBOT_FRAME_COORD_SPACE'] = str(args.coord_space)

    x: int | None = None
    y: int | None = None
    if gesture == 'shift_rmb':
        x = int(args.x) if args.x is not None else _env_int('FRBOT_LOOTING_BASIC_LOOT_X')
        y = int(args.y) if args.y is not None else _env_int('FRBOT_LOOTING_BASIC_LOOT_Y')
        if x is None or y is None:
            write_fatal('missing_precondition', details={'missing': ['FRBOT_LOOTING_BASIC_LOOT_X', 'FRBOT_LOOTING_BASIC_LOOT_Y']})
            return 1

    # Build ctx from env using the same config object as the standard gate.
    from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
    from runtime.env import parse_window_hwnd_env

    cfg = RuntimeConfig(
        mode='real',
        tick_hz=float(os.environ.get('FRBOT_TICK_HZ', '20.0') or '20.0'),
        config_path=str(os.environ.get('FRBOT_CONFIG_PATH', '') or ''),
        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,
        minimap_roi=str(os.environ.get('FRBOT_MINIMAP_ROI', 'minimap') or 'minimap'),
        window_hwnd=parse_window_hwnd_env('FRBOT_WINDOW_HWND'),
        window_title_substring=str(os.environ.get('FRBOT_WINDOW_TITLE', '') or ''),
        inventory_text_roi=str(os.environ.get('FRBOT_INVENTORY_TEXT_ROI', 'inventory_text') or 'inventory_text'),
        quick_loot_key=str(os.environ.get('FRBOT_QUICK_LOOT_KEY', 'R') or 'R'),
    )
    ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

    log_json(
        logger,
        event='start',
        gate='quick_loot_validation',
        gesture=str(gesture),
        x=None if x is None else int(x),
        y=None if y is None else int(y),
        coord_space=str(os.environ.get('FRBOT_FRAME_COORD_SPACE', 'frame') or 'frame'),
    )

    try:
        cap, inp, binding = looting_basic_preflight(ctx)
        binding.assert_bound()

        inv_roi = ctx.rois.get(ctx.config.inventory_text_roi)
        if inv_roi is None:
            raise PreflightFailed('looting_inventory_unreadable')

        before = cap.grab()
        record_before('quick_loot_validation', before)
        record_before('looting_basic', before)

        fw = int(getattr(before, 'width', 0) or 0)
        fh = int(getattr(before, 'height', 0) or 0)

        # Exactly one input.
        if gesture == 'shift_rmb':
            if x is None or y is None:
                raise PreflightFailed('looting_click_point_missing')
            if not hasattr(inp, 'shift_right_click_frame'):
                raise PreflightFailed('looting_input_not_supported')
            inp.shift_right_click_frame(int(x), int(y), frame_w=int(fw), frame_h=int(fh))
        else:
            if not hasattr(inp, 'alt_press_key'):
                raise PreflightFailed('looting_input_not_supported')
            inp.alt_press_key('q')
        ctx.looting.attempts_used += 1

        after = cap.grab()
        record_after('quick_loot_validation', after)
        record_after('looting_basic', after)

        evidence_kind = 'none'

        inv_pair = read_inventory_pair_binary(before, after, inv_roi)
        if inv_pair is not None:
            inv_before, inv_after = inv_pair
            delta = diff_inventory(inv_before, inv_after)
            if is_loot_success(delta):
                evidence_kind = 'inventory_delta'

        if evidence_kind == 'none':
            roi_name = (
                str(args.loot_panel_roi)
                if args.loot_panel_roi is not None
                else str(os.environ.get('FRBOT_LOOT_PANEL_OPEN_ROI', 'loot_container_open') or 'loot_container_open')
            )
            open_roi = ctx.rois.get(str(roi_name))
            if open_roi is not None:
                open_before = _read_bool_roi(before, open_roi)
                open_after = _read_bool_roi(after, open_roi)
                if open_before is False and open_after is True:
                    evidence_kind = 'loot_panel_open'

        if dump_enabled():
            dump_pair(gate='quick_loot_validation', before=before, after=after, reason=str(evidence_kind), out_dir=frames_dir)

        if int(getattr(ctx.looting, 'attempts_used', 0)) != 1:
            raise PreflightFailed('looting_basic_input_contract_violation')

        if evidence_kind == 'none':
            raise PreflightFailed('quick_loot_not_effective')

        log_json(logger, event='success', gate='quick_loot_validation', evidence_kind=str(evidence_kind))
        return 0

    except PreflightFailed as exc:
        # Always leave auditable evidence when possible.
        try:
            from diagnostics.last_frames import snapshot

            before_f, after_f = snapshot('quick_loot_validation')
            if dump_enabled() and (before_f is not None or after_f is not None):
                dump_pair(gate='quick_loot_validation', before=before_f, after=after_f, reason=str(exc), out_dir=frames_dir)
        except Exception:
            pass

        write_fatal(
            str(exc),
            exc,
            details={
                'gate': 'quick_loot_validation',
                'gesture': str(gesture),
                'attempts_used': int(getattr(ctx.looting, 'attempts_used', 0)),
                'x': None if x is None else int(x),
                'y': None if y is None else int(y),
            },
        )
        return 1

    except Exception as exc:
        write_fatal('runtime crashed', exc, details={'gate': 'quick_loot_validation'})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
