from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TypeAlias

from adapters.capture.mock_world_any import MockWorldAnyCapture
from adapters.capture.mss_bound_window_real import MssBoundWindowRealCapture
from adapters.capture.meld_real import MeldBoundWindowRealCapture
from adapters.capture.obs_source_real import ObsSourceRealCapture
from adapters.input.mock_world import MockWorldInput
from adapters.input.win32_hwnd import Win32HwndKeyboard
from adapters.mock_world import MockBattleListRow, MockWorld
from adapters.window.mock import MockWindowBinding
from adapters.window.win32 import Win32WindowBinding
from contracts.capture import CaptureStatus, Frame
from contracts.evidence import Roi
from contracts.errors import PreflightFailed
from contracts.input import InputStatus
from contracts.runtime import RuntimeContext, RuntimeState
from diagnostics.frame_dump import dump_frame_ppm, dump_pair
from runtime.battle_list_semantics import crop_roi_rgb, detect_battle_list
from runtime.config_loader import load_rois
from runtime.startup_guards import enforce_prod_emergency_real_startup_guards
from runtime.capture_source import capture_source, resolve_input_hwnd, resolve_obs_projector_hwnd
from runtime.roi_contract import validate_prod_emergency_real_rois_in_bounds


CaptureAdapter: TypeAlias = MssBoundWindowRealCapture | MeldBoundWindowRealCapture | ObsSourceRealCapture | MockWorldAnyCapture
InputAdapter: TypeAlias = Win32HwndKeyboard | MockWorldInput
WindowBindingAdapter: TypeAlias = Win32WindowBinding | MockWindowBinding


def _evidence_dir_default() -> Path:
    raw = (os.environ.get('FRBOT_REAL_FRAMES_DIR', '') or '').strip()
    if raw:
        return Path(str(raw))
    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    if profile == 'prod_emergency':
        return Path('diagnostics') / 'frames_emergency'
    if profile == 'prod_full':
        return Path('diagnostics') / 'frames_full'
    return Path('diagnostics') / 'frames'


def _dump_battle_list_debug(*, gate: str, frame: Frame, roi: Roi, reason: str) -> None:
    try:
        out_dir = _evidence_dir_default()
        out_dir.mkdir(parents=True, exist_ok=True)

        # Full-frame evidence (preflight can fail before last_frames snapshot exists).
        dump_pair(gate=str(gate), before=frame, after=None, reason=str(reason), out_dir=out_dir)

        rgb = crop_roi_rgb(frame, roi)
        if rgb and int(roi.width) > 0 and int(roi.height) > 0:
            crop = Frame(width=int(roi.width), height=int(roi.height), monotonic_ts_ns=0, digest_hex='', rgb=bytes(rgb))
            stamp = int(time.time())
            dump_frame_ppm(crop, out_dir / f'{str(gate).strip().lower()}_{stamp}_battle_list_roi.ppm')

        # Probe the exact pixels used by the mock OCR in row 0.
        samples: list[dict[str, int]] = []
        w = int(roi.width)
        for i in range(12):
            x = 2 + i
            y = 4
            r, g, b = (0, 0, 0)
            if rgb and w > 0 and x >= 0 and y >= 0 and x < w:
                idx = (y * w + x) * 3
                if 0 <= idx and (idx + 2) < len(rgb):
                    r = int(rgb[idx])
                    g = int(rgb[idx + 1])
                    b = int(rgb[idx + 2])
            samples.append({'x': int(x), 'y': int(y), 'r': int(r), 'g': int(g), 'b': int(b)})

        (out_dir / f'{str(gate).strip().lower()}_battle_list_debug.json').write_text(
            json.dumps(
                {
                    'reason': str(reason),
                    'frame_resolution': [int(getattr(frame, 'width', 0)), int(getattr(frame, 'height', 0))],
                    'roi': {'x': int(roi.x), 'y': int(roi.y), 'width': int(roi.width), 'height': int(roi.height)},
                    'mock_ocr_probe_samples': samples,
                    'note': 'If g/b are not 0 and r are not ASCII bytes, the mock OCR encoding is not present in this ROI.',
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + '\n',
            encoding='utf-8',
        )
    except Exception:
        return


def _parse_mock_rows(raw: str) -> tuple[MockBattleListRow, ...]:
    # Format: "Name:hp:atk;Name2:hp:atk" where hp/atk are 0/1.
    s = (raw or '').strip()
    if not s:
        return ()
    rows: list[MockBattleListRow] = []
    for part in s.split(';'):
        p = part.strip()
        if not p:
            continue
        bits = [b.strip() for b in p.split(':')]
        if len(bits) < 1:
            continue
        name = bits[0]
        hp = bits[1] if len(bits) >= 2 else '1'
        atk = bits[2] if len(bits) >= 3 else '1'
        rows.append(
            MockBattleListRow(
                name=str(name),
                hp_bar_visible=(hp == '1'),
                is_attackable=(atk == '1'),
            )
        )
    return tuple(rows)


def targeting_preflight(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    """Targeting-only preflight.

    Invariant: without semantic Battle List evidence -> abort.
    """

    ctx.status.state = RuntimeState.PREFLIGHT
    ctx.status.reason = ''

    mode = ctx.config.mode.strip().lower()

    if mode == 'real':
        enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=False)

    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)

    battle_roi = ctx.rois.get(ctx.config.battle_list_roi)
    if battle_roi is None:
        raise PreflightFailed('battle_list_not_detected')

    if mode == 'real':
        backend = (os.environ.get('FRBOT_CAPTURE_BACKEND', 'mss') or 'mss').strip().lower()
        profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        if profile == 'prod_emergency' and backend not in {'mss', 'meld'}:
            raise PreflightFailed('capture_invalid')

        cap_source = capture_source()

        # InputAuthority (strict): always bind to Tibia HWND/title; capture is separate.
        binding_real = Win32WindowBinding(
            hwnd=int(ctx.config.window_hwnd),
            title_substring=ctx.config.window_title_substring,
        )
        bvr = binding_real.verify()
        if not bvr.ok:
            raise PreflightFailed('targeting_window_binding_lost')

        capture_real: MssBoundWindowRealCapture | MeldBoundWindowRealCapture | ObsSourceRealCapture

        if cap_source == 'obs_source':
            src = (os.environ.get('FRBOT_OBS_SOURCE_NAME', '') or '').strip()
            if not src:
                raise PreflightFailed('obs_source_not_found')
            if loaded.frame_width is None or loaded.frame_height is None:
                raise PreflightFailed('config_invalid_schema')

            capture_real = ObsSourceRealCapture(
                obs_source_name=str(src),
                expected_width=int(loaded.frame_width),
                expected_height=int(loaded.frame_height),
                rois=ctx.rois,
                minimap_roi_name=str(ctx.config.minimap_roi),
            )
        else:
            cap_binding = binding_real
            if cap_source == 'obs':
                obs_hwnd, _obs_title = resolve_obs_projector_hwnd()
                cap_binding = Win32WindowBinding(hwnd=int(obs_hwnd), title_substring=(os.environ.get('FRBOT_OBS_PROJECTOR_TITLE', '') or ''))

            if backend == 'meld':
                try:
                    capture_real = MeldBoundWindowRealCapture(binding=cap_binding)
                except ImportError as exc:
                    raise PreflightFailed('capture_black_or_unavailable') from exc
            else:
                try:
                    capture_real = MssBoundWindowRealCapture(binding=cap_binding)
                except ImportError as exc:
                    raise PreflightFailed(str(exc)) from exc

        try:
            input_hwnd = resolve_input_hwnd(hwnd=int(ctx.config.window_hwnd), title_substring=ctx.config.window_title_substring)
            if input_hwnd <= 0:
                raise PreflightFailed('targeting_window_binding_lost')
            input_real = Win32HwndKeyboard(hwnd=int(input_hwnd))
        except Exception as exc:
            raise PreflightFailed(f'failed to initialize win32 input: {type(exc).__name__}: {exc}') from exc

        cap_v = capture_real.verify()
        inp_v = input_real.verify()

        ctx.capture = CaptureStatus(backend=capture_real.name, verified=cap_v.ok)
        ctx.input = InputStatus(backend=input_real.name, verified=inp_v.ok)

        if not cap_v.ok:
            profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
            if profile == 'prod_emergency':
                if cap_source == 'obs' and (cap_v.reason or '') in {'captured_frame_black', 'capture_black_or_unavailable', 'frame_empty'}:
                    raise PreflightFailed('captured_frame_black_obs')
                if cap_source == 'obs_source' and (cap_v.reason or ''):
                    raise PreflightFailed(str(cap_v.reason))
                raise PreflightFailed('capture_invalid')
            raise PreflightFailed(cap_v.reason or 'capture not verified')
        if not inp_v.ok:
            raise PreflightFailed(inp_v.reason or 'input not verified')

        try:
            binding_real.assert_bound()
        except Exception:
            raise PreflightFailed('targeting_window_binding_lost')

        before = capture_real.grab()
        validate_prod_emergency_real_rois_in_bounds(rois=ctx.rois, frame=before)

        obs = detect_battle_list(before, battle_roi)
        if obs is None:
            _dump_battle_list_debug(gate='targeting_full_preflight', frame=before, roi=battle_roi, reason='battle_list_not_detected')
            raise PreflightFailed('battle_list_not_detected')

        ctx.status.state = RuntimeState.READY
        return capture_real, input_real, binding_real

    # mock mode
    binding_mock = MockWindowBinding()
    bvr = binding_mock.verify()
    if not bvr.ok:
        raise PreflightFailed('targeting_window_binding_lost')

    cap_ok = os.environ.get('FRBOT_MOCK_CAPTURE_OK', '1') == '1'
    inp_ok = os.environ.get('FRBOT_MOCK_INPUT_OK', '1') == '1'

    rows = _parse_mock_rows(os.environ.get('FRBOT_MOCK_BATTLE_LIST_ROWS', ''))
    selected_raw = os.environ.get('FRBOT_MOCK_BATTLE_SELECTED_ROW', '')
    selected = int(selected_raw) if selected_raw.strip().isdigit() else None
    click_behavior = os.environ.get('FRBOT_MOCK_BATTLE_CLICK_BEHAVIOR', 'normal')

    world = MockWorld.create(
        rois=ctx.rois,
        key_kinds={},
        minimap_noise=False,
        battle_list_rows=rows,
        battle_list_selected_row=selected,
        battle_list_row_height=16,
        click_behavior=click_behavior,
    )
    world.on_noop()

    capture_mock = MockWorldAnyCapture(world=world, verified=cap_ok)
    input_mock = MockWorldInput(world=world, verified=inp_ok)

    verified_capture = bool(capture_mock.verify().ok)
    verified_input = bool(input_mock.verify().ok)

    ctx.capture = CaptureStatus(backend=capture_mock.name, verified=verified_capture)
    ctx.input = InputStatus(backend=input_mock.name, verified=verified_input)

    if not verified_capture:
        raise PreflightFailed('capture not verified')
    if not verified_input:
        raise PreflightFailed('input not verified')

    before = capture_mock.grab()
    # Mock-only: keep legacy bounds check for the specific ROI we need.
    if (battle_roi.x + battle_roi.width) > before.width or (battle_roi.y + battle_roi.height) > before.height:
        raise PreflightFailed('targeting_window_binding_lost')

    obs = detect_battle_list(before, battle_roi)
    if obs is None:
        raise PreflightFailed('battle_list_not_detected')

    ctx.status.state = RuntimeState.READY
    return capture_mock, input_mock, binding_mock


def run(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    return targeting_preflight(ctx)
