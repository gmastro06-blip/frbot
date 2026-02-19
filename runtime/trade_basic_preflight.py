from __future__ import annotations

import os
from typing import TypeAlias

from adapters.capture.mock_world_any import MockWorldAnyCapture
from adapters.capture.obs_source_real import ObsSourceRealCapture
from adapters.capture.mss_bound_window_real import MssBoundWindowRealCapture
from adapters.input.mock_world import MockWorldInput
from adapters.input.win32_hwnd import Win32HwndKeyboard
from adapters.mock_world import MockWorld
from adapters.window.mock import MockWindowBinding
from adapters.window.win32 import Win32WindowBinding
from contracts.capture import CaptureStatus
from contracts.errors import PreflightFailed
from contracts.input import InputStatus
from contracts.runtime import RuntimeContext, RuntimeState
from runtime.capture_source import capture_source, resolve_input_hwnd, resolve_obs_projector_hwnd
from runtime.config_loader import load_rois
from runtime.roi_contract import validate_prod_emergency_real_rois_in_bounds
from runtime.startup_guards import enforce_prod_emergency_real_startup_guards
from runtime.trade_semantics import detect_npc_window, read_trade_inventory


CaptureAdapter: TypeAlias = MssBoundWindowRealCapture | ObsSourceRealCapture | MockWorldAnyCapture
InputAdapter: TypeAlias = Win32HwndKeyboard | MockWorldInput
WindowBindingAdapter: TypeAlias = Win32WindowBinding | MockWindowBinding


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def trade_basic_preflight(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    """Preflight for trade_basic.

    Invariants:
    - Strong adapters verified (capture + input + window binding)
    - NPC window is semantically detected and matches expected NPC id
    - Trade inventory is semantically readable

    Note: PROD-EMERGENCY REAL capture is OBS source identity.
    """

    ctx.status.state = RuntimeState.PREFLIGHT
    ctx.status.reason = ''

    mode = str(ctx.config.mode).strip().lower()

    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)
    ctx.frame_width = loaded.frame_width
    ctx.frame_height = loaded.frame_height

    inv_roi = ctx.rois.get(str(ctx.config.trade_inventory_roi))
    npc_roi = ctx.rois.get(str(ctx.config.trade_npc_roi))
    action_roi = ctx.rois.get(str(ctx.config.trade_action_roi))
    if inv_roi is None or npc_roi is None or action_roi is None:
        raise PreflightFailed('trade_unverified_action')

    # Reset trade state.
    ctx.trade.attempts_used = 0
    ctx.trade.inputs_sent = 0
    ctx.trade.last_npc = None
    ctx.trade.last_inventory_before = None
    ctx.trade.last_inventory_after = None

    if mode == 'real':
        enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=False)

        profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        pixel_fallback_ok = profile == 'prod_full'

        cap_source = capture_source()

        binding_hwnd = int(ctx.config.window_hwnd)
        binding_title = str(ctx.config.window_title_substring)
        binding_real = Win32WindowBinding(hwnd=int(binding_hwnd), title_substring=binding_title)
        bvr = binding_real.verify()
        if not bvr.ok:
            raise PreflightFailed('trade_window_binding_lost')

        input_hwnd = resolve_input_hwnd(hwnd=int(ctx.config.window_hwnd), title_substring=str(ctx.config.window_title_substring))
        if int(input_hwnd) <= 0:
            raise PreflightFailed('trade_window_binding_lost')

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
                minimap_roi_name=str(getattr(ctx.config, 'minimap_roi', 'minimap') or 'minimap'),
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
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
            raise PreflightFailed(f'failed to initialize win32 input: {type(exc).__name__}: {exc}') from exc

        cap_v = capture_real.verify()
        inp_v = input_real.verify()
        ctx.capture = CaptureStatus(backend=capture_real.name, verified=cap_v.ok)
        ctx.input = InputStatus(backend=input_real.name, verified=inp_v.ok)

        if not cap_v.ok:
            raise PreflightFailed(cap_v.reason or 'capture not verified')
        if not inp_v.ok:
            raise PreflightFailed(inp_v.reason or 'input not verified')

        try:
            binding_real.assert_bound()
        except Exception as exc:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise

            raise PreflightFailed('trade_window_binding_lost') from exc

        f = capture_real.grab()
        validate_prod_emergency_real_rois_in_bounds(rois=ctx.rois, frame=f)

        if not pixel_fallback_ok:
            npc = detect_npc_window(f, npc_roi)
            if npc is None:
                raise PreflightFailed('trade_npc_not_detected')
            if int(npc.npc_id) != int(ctx.config.trade_expected_npc_id):
                raise PreflightFailed('trade_wrong_npc')

            inv = read_trade_inventory(f, inv_roi)
            if inv is None:
                raise PreflightFailed('trade_unverified_action')

            ctx.trade.last_npc = npc
            ctx.trade.last_inventory_before = inv

        ctx.status.state = RuntimeState.READY
        return capture_real, input_real, binding_real

    # mock
    binding_mock = MockWindowBinding()
    bvr = binding_mock.verify()
    if not bvr.ok:
        raise PreflightFailed('trade_window_binding_lost')

    cap_ok = os.environ.get('FRBOT_MOCK_CAPTURE_OK', '1') == '1'
    inp_ok = os.environ.get('FRBOT_MOCK_INPUT_OK', '1') == '1'

    world = MockWorld.create(
        rois=ctx.rois,
        minimap_noise=False,
        battle_list_rows=(),
        battle_list_selected_row=None,
        battle_list_row_height=16,
        click_behavior='normal',
    )

    world.trade_npc_present = _env_bool('MOCK_TRADE_NPC_PRESENT', True)
    world.trade_npc_id = int(ctx.config.trade_expected_npc_id) + 1 if _env_bool('MOCK_TRADE_WRONG_NPC', False) else int(ctx.config.trade_expected_npc_id)
    world.trade_npc_open = True
    world.trade_action = str(ctx.config.trade_action)

    world.trade_gold = int(os.environ.get('FRBOT_MOCK_TRADE_GOLD', '100') or '100')
    world.trade_item_count = int(os.environ.get('FRBOT_MOCK_TRADE_ITEMS', '0') or '0')
    world.trade_capacity_used = int(os.environ.get('FRBOT_MOCK_TRADE_CAP_USED', '0') or '0')

    world.mock_trade_buy_ok = _env_bool('MOCK_TRADE_BUY_OK', False)
    world.mock_trade_sell_ok = _env_bool('MOCK_TRADE_SELL_OK', False)
    world.mock_trade_no_delta = _env_bool('MOCK_TRADE_NO_DELTA', False)
    world.mock_trade_gold_only = _env_bool('MOCK_TRADE_GOLD_ONLY', False)
    world.mock_trade_item_only = _env_bool('MOCK_TRADE_ITEM_ONLY', False)

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

    f = capture_mock.grab()
    npc = detect_npc_window(f, npc_roi)
    if npc is None:
        raise PreflightFailed('trade_npc_not_detected')
    if int(npc.npc_id) != int(ctx.config.trade_expected_npc_id):
        raise PreflightFailed('trade_wrong_npc')

    inv = read_trade_inventory(f, inv_roi)
    if inv is None:
        raise PreflightFailed('trade_unverified_action')

    ctx.trade.last_npc = npc
    ctx.trade.last_inventory_before = inv

    ctx.status.state = RuntimeState.READY
    return capture_mock, input_mock, binding_mock


def run(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    return trade_basic_preflight(ctx)
