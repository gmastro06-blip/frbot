from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypeAlias

from adapters.capture.mock_world_any import MockWorldAnyCapture
from adapters.capture.obs_source_real import ObsSourceRealCapture
from adapters.capture.mss_bound_window_real import MssBoundWindowRealCapture
from adapters.input.mock_world import MockWorldInput
from adapters.input.win32_hwnd import Win32HwndKeyboard
from adapters.mock_world import MockWorld
from adapters.window.mock import MockWindowBinding
from adapters.window.win32 import Win32WindowBinding
from contracts.capture import CaptureStatus, Frame
from contracts.errors import PreflightFailed
from contracts.input import InputStatus
from contracts.runtime import RuntimeContext, RuntimeState
from runtime.capture_source import capture_source, resolve_input_hwnd, resolve_obs_projector_hwnd
from runtime.config_loader import load_rois
from runtime.inventory_semantics import (
    beef_candidate_u16,
    rank_beef_candidates_by_temporal_stability,
    rank_beef_candidates_by_temporal_stability_fast,
    read_inventory_binary,
    scan_beef_candidates_in_frame,
)
from runtime.roi_contract import validate_prod_emergency_real_rois_in_bounds
from runtime.startup_guards import enforce_prod_emergency_real_startup_guards


CaptureAdapter: TypeAlias = MssBoundWindowRealCapture | ObsSourceRealCapture | MockWorldAnyCapture
InputAdapter: TypeAlias = Win32HwndKeyboard | MockWorldInput
WindowBindingAdapter: TypeAlias = Win32WindowBinding | MockWindowBinding


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def looting_basic_preflight(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    """Preflight for looting_basic.

    Invariants:
    - Strong adapters verified (capture + input + window binding)
    - `inventory_text` ROI exists and is semantically readable immediately
    """

    ctx.status.state = RuntimeState.PREFLIGHT
    ctx.status.reason = ''

    mode = str(ctx.config.mode).strip().lower()

    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)
    ctx.frame_width = loaded.frame_width
    ctx.frame_height = loaded.frame_height

    inv_name = str(ctx.config.inventory_text_roi)
    inv_roi = ctx.rois.get(inv_name)
    if inv_roi is None:
        raise PreflightFailed('looting_inventory_unreadable')

    chat_roi_name = (os.environ.get('FRBOT_CHAT_LOOT_ROI', '') or 'chat_loot_area').strip() or 'chat_loot_area'
    chat_roi = ctx.rois.get(chat_roi_name)

    if mode == 'real':
        enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=False)

        cap_source = capture_source()

        binding_hwnd = int(ctx.config.window_hwnd)
        binding_title = str(ctx.config.window_title_substring)
        binding_real = Win32WindowBinding(hwnd=int(binding_hwnd), title_substring=binding_title)
        bvr = binding_real.verify()
        if not bvr.ok:
            raise PreflightFailed('looting_window_binding_lost')

        input_hwnd = resolve_input_hwnd(hwnd=int(ctx.config.window_hwnd), title_substring=str(ctx.config.window_title_substring))
        if int(input_hwnd) <= 0:
            raise PreflightFailed('looting_window_binding_lost')

        capture_real: MssBoundWindowRealCapture | ObsSourceRealCapture
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
                cap_binding = Win32WindowBinding(hwnd=int(obs_hwnd), title_substring=os.environ.get('FRBOT_OBS_PROJECTOR_TITLE', '') or '')
            capture_real = MssBoundWindowRealCapture(binding=cap_binding)

        try:
            input_real = Win32HwndKeyboard(hwnd=int(input_hwnd))
        except Exception as exc:
            raise PreflightFailed(f'failed to initialize win32 input: {type(exc).__name__}: {exc}') from exc

        try:
            cap_v = capture_real.verify()
        except PreflightFailed as exc:
            r = str(exc)
            if cap_source == 'obs_source' and r.startswith('obs_ws_'):
                raise PreflightFailed('obs_ws_unreachable') from exc
            raise
        inp_v = input_real.verify()
        ctx.capture = CaptureStatus(backend=capture_real.name, verified=cap_v.ok)
        ctx.input = InputStatus(backend=input_real.name, verified=inp_v.ok)

        if not cap_v.ok:
            raise PreflightFailed(cap_v.reason or 'capture not verified')
        if not inp_v.ok:
            raise PreflightFailed(inp_v.reason or 'input not verified')
        try:
            binding_real.assert_bound()
        except Exception:
            raise PreflightFailed('looting_window_binding_lost')

        try:
            f0 = capture_real.grab()
        except PreflightFailed as exc:
            r = str(exc)
            if cap_source == 'obs_source' and r.startswith('obs_ws_'):
                raise PreflightFailed('obs_ws_unreachable') from exc
            raise
        validate_prod_emergency_real_rois_in_bounds(rois=ctx.rois, frame=f0)

        profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        is_emergency = profile == 'prod_emergency'

        if is_emergency and chat_roi is None:
            raise PreflightFailed('looting_chat_unreadable')

        if is_emergency:
            try:
                from runtime.chat_loot_semantics import _crop_rgb as _crop_chat

                if chat_roi is None or not _crop_chat(f0, chat_roi):
                    raise PreflightFailed('looting_chat_unreadable')
            except PreflightFailed:
                raise
            except Exception:
                raise PreflightFailed('looting_chat_unreadable')

        # Certification contract: we cannot introspect Tibia config.
        # Require explicit operator confirmation of the intended loot action.
        action = (os.environ.get('FRBOT_LOOTING_BASIC_ACTION', '') or '').strip().lower()
        if not action:
            action = (os.environ.get('FRBOT_TIBIA_LOOT_GESTURE', '') or 'shift_rmb').strip().lower()

        if profile == 'prod_emergency':
            action = 'alt_q'

        gesture = str(action)
        if is_emergency:
            if gesture not in {'shift_rmb', 'rmb', 'key', 'alt_q'}:
                raise PreflightFailed('looting_action_not_configured')

        # Verify required input capability exists for the configured gesture.
        if gesture == 'shift_rmb':
            if not hasattr(input_real, 'shift_right_click_frame'):
                raise PreflightFailed('looting_input_not_supported')
        elif gesture == 'rmb':
            if not hasattr(input_real, 'right_click_frame'):
                raise PreflightFailed('looting_input_not_supported')
        elif gesture == 'key':
            if not hasattr(input_real, 'press_key'):
                raise PreflightFailed('looting_input_not_supported')
        elif gesture == 'alt_q':
            if not hasattr(input_real, 'press_combo'):
                raise PreflightFailed('looting_input_not_supported')

        # Use the same cap_used plausibility threshold as the binary decoder.
        try:
            cap_max = int((os.environ.get('FRBOT_INVENTORY_BINARY_CAP_MAX', '50000') or '50000').strip() or '50000')
        except Exception:
            cap_max = 50000
        cap_max = max(1, min(int(cap_max), 65535))

        # PROD-EMERGENCY contract: binary inventory encoding is carried in a 2x1 ROI (6 bytes).
        # If the ROI is not calibrated yet, dump evidence to help calibration.
        if is_emergency and (int(getattr(inv_roi, 'width', 0) or 0) != 2 or int(getattr(inv_roi, 'height', 0) or 0) != 1):
            try:
                from diagnostics.frame_dump import dump_frame_ppm

                out_dir = Path('diagnostics') / 'frames_emergency'
                out_dir.mkdir(parents=True, exist_ok=True)
                dump_frame_ppm(f0, out_dir / 'emergency_inventory_binary_calibration_frame.ppm')

                # Provide a best-effort candidate list for ROI placement.
                candidates = scan_beef_candidates_in_frame(f0, limit=200, cap_max=int(cap_max), gold_max=None)
                raw_candidates = scan_beef_candidates_in_frame(f0, limit=50, cap_max=None, gold_max=None)

                after_frames: list[Frame] = []
                try:
                    for _i in range(5):
                        after_frames.append(capture_real.grab())
                except Exception:
                    after_frames = []
                stable = rank_beef_candidates_by_temporal_stability(
                    before=f0,
                    after_frames=after_frames,
                    cap_max=int(cap_max),
                    gold_max=None,
                    top_n=50,
                )

                (out_dir / 'emergency_inventory_binary_beef_candidates.json').write_text(
                    json.dumps(
                        {
                            'note': 'Candidates for pixel-aligned 0xBEEF (EF BE) in the full frame. Use cap_max to reduce false positives.',
                            'frame_name': 'preflight_f0',
                            'filtered_cap_max': int(cap_max),
                            'filtered': [
                                {
                                    'x': int(c.x),
                                    'y': int(c.y),
                                    'w': 2,
                                    'h': 1,
                                    'raw6_hex': str(c.raw6_hex),
                                    'u16': beef_candidate_u16(str(c.raw6_hex)),
                                }
                                for c in candidates
                            ],
                            'unfiltered_sample': [
                                {
                                    'x': int(c.x),
                                    'y': int(c.y),
                                    'w': 2,
                                    'h': 1,
                                    'raw6_hex': str(c.raw6_hex),
                                    'u16': beef_candidate_u16(str(c.raw6_hex)),
                                }
                                for c in raw_candidates
                            ],
                            'stable_top': stable,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + '\n',
                    encoding='utf-8',
                )
            except Exception:
                # Best-effort diagnostics: failures while dumping emergency calibration
                # evidence must not mask the primary preflight failure.
                pass
            raise PreflightFailed('looting_inventory_unreadable')

        # PROD-EMERGENCY contract: binary inventory overlay must exist in the capture.
        # Dump candidate evidence unconditionally and hard-stop if no coherent
        # candidates are observed across BEFORE/AFTER frames.
        if is_emergency:
            try:
                out_dir = Path('diagnostics') / 'frames_emergency'
                out_dir.mkdir(parents=True, exist_ok=True)

                before_hits = scan_beef_candidates_in_frame(f0, limit=200, cap_max=int(cap_max), gold_max=None)

                after_frames3: list[Frame] = []
                try:
                    for _i in range(3):
                        after_frames3.append(capture_real.grab())
                except Exception:
                    after_frames3 = []

                stable_fast = rank_beef_candidates_by_temporal_stability_fast(
                    before=f0,
                    after_frames=[af for af in after_frames3 if af is not None],
                    cap_max=int(cap_max),
                    gold_max=None,
                    top_n=50,
                    scan_limit=200,
                )

                (out_dir / 'emergency_inventory_binary_beef_candidates.json').write_text(
                    json.dumps(
                        {
                            'gate': 'looting_basic',
                            'reason': 'preflight_overlay_check',
                            'cap_max': int(cap_max),
                            'before': {
                                'frame_name': 'preflight_f0',
                                'count': int(len(before_hits)),
                                'candidates': [
                                    {
                                        'x': int(c.x),
                                        'y': int(c.y),
                                        'w': 2,
                                        'h': 1,
                                        'raw6_hex': str(c.raw6_hex),
                                        'u16': beef_candidate_u16(str(c.raw6_hex)),
                                    }
                                    for c in before_hits
                                ],
                            },
                            'after_frames_count': int(len(after_frames3)),
                            'stable_top': stable_fast,
                            'stable_method': 'fast_limited_scan',
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + '\n',
                    encoding='utf-8',
                )

                if not stable_fast:
                    raise PreflightFailed('inventory_overlay_missing')
            except PreflightFailed:
                raise
            except Exception:
                raise PreflightFailed('inventory_overlay_missing')

        if read_inventory_binary(f0, inv_roi) is None:
            if is_emergency:
                try:
                    from diagnostics.frame_dump import dump_frame_ppm

                    out_dir = Path('diagnostics') / 'frames_emergency'
                    out_dir.mkdir(parents=True, exist_ok=True)
                    dump_frame_ppm(f0, out_dir / 'emergency_inventory_binary_unreadable_frame.ppm')
                    candidates = scan_beef_candidates_in_frame(f0, limit=200, cap_max=int(cap_max), gold_max=None)

                    after_frames2: list[Frame] = []
                    try:
                        for _i in range(5):
                            after_frames2.append(capture_real.grab())
                    except Exception:
                        after_frames2 = []
                    stable2 = rank_beef_candidates_by_temporal_stability(
                        before=f0,
                        after_frames=after_frames2,
                        cap_max=int(cap_max),
                        gold_max=None,
                        top_n=50,
                    )

                    (out_dir / 'emergency_inventory_binary_beef_candidates.json').write_text(
                        json.dumps(
                            {
                                'note': 'Binary inventory unreadable at configured ROI; candidates list may help update inventory_text ROI.',
                                'frame_name': 'preflight_f0',
                                'filtered_cap_max': int(cap_max),
                                'filtered': [
                                    {
                                        'x': int(c.x),
                                        'y': int(c.y),
                                        'w': 2,
                                        'h': 1,
                                        'raw6_hex': str(c.raw6_hex),
                                        'u16': beef_candidate_u16(str(c.raw6_hex)),
                                    }
                                    for c in candidates
                                ],
                                'stable_top': stable2,
                            },
                            indent=2,
                            sort_keys=True,
                        )
                        + '\n',
                        encoding='utf-8',
                    )
                except Exception:
                    # Best-effort diagnostics: failure to dump calibration hints is non-fatal.
                    pass
            raise PreflightFailed('looting_inventory_unreadable')

        ctx.status.state = RuntimeState.READY
        return capture_real, input_real, binding_real

    # mock
    binding_mock = MockWindowBinding()
    bvr = binding_mock.verify()
    if not bvr.ok:
        raise PreflightFailed('looting_window_binding_lost')

    cap_ok = os.environ.get('FRBOT_MOCK_CAPTURE_OK', '1') == '1'
    inp_ok = os.environ.get('FRBOT_MOCK_INPUT_OK', '1') == '1'

    quick_loot_key = str(ctx.config.quick_loot_key)
    key_kinds = {quick_loot_key: 'loot'} if quick_loot_key else {}

    world = MockWorld.create(
        rois=ctx.rois,
        key_kinds=key_kinds,
        minimap_noise=False,
        battle_list_rows=(),
        battle_list_selected_row=None,
        battle_list_row_height=16,
        click_behavior='normal',
        inventory_gold_count=int(os.environ.get('FRBOT_MOCK_INV_GOLD', '0') or '0'),
        inventory_capacity_used=int(os.environ.get('FRBOT_MOCK_INV_CAP_USED', '0') or '0'),
        mock_loot_inventory_delta=_env_bool('MOCK_LOOT_INVENTORY_DELTA', False),
        mock_loot_inventory_delta_count=max(0, int(os.environ.get('MOCK_LOOT_INVENTORY_DELTA_COUNT', '0') or '0')),
        mock_loot_container_opens=False,
        mock_loot_inventory_read_fail=_env_bool('MOCK_LOOT_INVENTORY_READ_FAIL', False),
        mock_loot_premium=True,
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

    f0 = capture_mock.grab()
    if read_inventory_binary(f0, inv_roi) is None:
        raise PreflightFailed('looting_inventory_unreadable')

    ctx.status.state = RuntimeState.READY
    return capture_mock, input_mock, binding_mock


def run(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    return looting_basic_preflight(ctx)
