from __future__ import annotations

import os
from typing import TypeAlias

from adapters.capture.mock_world_any import MockWorldAnyCapture
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
from runtime.config_loader import load_rois
from runtime.depot_semantics import read_depot_container
from runtime.inventory_semantics import read_inventory


CaptureAdapter: TypeAlias = MssBoundWindowRealCapture | MockWorldAnyCapture
InputAdapter: TypeAlias = Win32HwndKeyboard | MockWorldInput
WindowBindingAdapter: TypeAlias = Win32WindowBinding | MockWindowBinding


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


def deposit_preflight(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    """Deposit gate preflight.

    Invariants:
    - Strong window binding
    - Inventory ROI exists and is semantically readable
    - Depot container ROI exists and is semantically readable
    - Depot must be open (explicit semantic bit)

    Preflight must not create runtime.log.
    """

    ctx.status.state = RuntimeState.PREFLIGHT
    ctx.status.reason = ''

    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)

    inv_roi = ctx.rois.get(ctx.config.inventory_text_roi)
    depot_roi = ctx.rois.get(ctx.config.depot_container_roi)

    if inv_roi is None or depot_roi is None:
        raise PreflightFailed('deposit_inventory_unreadable')

    mode = ctx.config.mode.strip().lower()

    # Reset deposit state.
    ctx.deposit.attempts_used = 0
    ctx.deposit.inputs_sent = 0
    ctx.deposit.last_inventory_before = None
    ctx.deposit.last_inventory_after = None
    ctx.deposit.last_depot_before = None
    ctx.deposit.last_depot_after = None

    if mode == 'real':
        binding_real = Win32WindowBinding(hwnd=int(ctx.config.window_hwnd), title_substring=ctx.config.window_title_substring)
        bvr = binding_real.verify()
        if not bvr.ok:
            raise PreflightFailed('deposit_window_binding_lost')

        try:
            capture_real = MssBoundWindowRealCapture(binding=binding_real)
        except ImportError as exc:
            raise PreflightFailed(str(exc)) from exc

        snap = binding_real.snapshot()
        try:
            input_real = Win32HwndKeyboard(hwnd=int(snap.hwnd))
        except Exception as exc:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise

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

            raise PreflightFailed('deposit_window_binding_lost') from exc

        f = capture_real.grab()

        inv = read_inventory(f, inv_roi)
        if inv is None:
            raise PreflightFailed('deposit_inventory_unreadable')

        depot = read_depot_container(f, depot_roi)
        if depot is None:
            raise PreflightFailed('deposit_unreadable_state')

        if not bool(depot.open):
            raise PreflightFailed('deposit_depot_not_open')

        ctx.status.state = RuntimeState.READY
        return capture_real, input_real, binding_real

    # mock
    binding_mock = MockWindowBinding()
    bvr = binding_mock.verify()
    if not bvr.ok:
        raise PreflightFailed('deposit_window_binding_lost')

    cap_ok = os.environ.get('FRBOT_MOCK_CAPTURE_OK', '1') == '1'
    inp_ok = os.environ.get('FRBOT_MOCK_INPUT_OK', '1') == '1'

    deposit_key = str(ctx.config.deposit_key)
    key_kinds = {deposit_key: 'deposit'} if deposit_key else {}

    world = MockWorld.create(
        rois=ctx.rois,
        key_kinds=key_kinds,
        minimap_noise=False,
        battle_list_rows=(),
        battle_list_selected_row=None,
        battle_list_row_height=16,
        click_behavior='normal',
        inventory_gold_count=int(os.environ.get('FRBOT_MOCK_INV_GOLD', '5') or '5'),
        inventory_capacity_used=int(os.environ.get('FRBOT_MOCK_INV_CAP_USED', '5') or '5'),
    )
    world.depot_item_count = int(os.environ.get('FRBOT_MOCK_DEPOT_COUNT', '0') or '0')
    world.depot_open = not _env_bool('MOCK_DEPOSIT_DEPOT_CLOSED', False)
    world.mock_deposit_success = _env_bool('MOCK_DEPOSIT_SUCCESS', False)
    world.mock_deposit_no_delta = _env_bool('MOCK_DEPOSIT_NO_DELTA', False)
    world.mock_deposit_partial = _env_bool('MOCK_DEPOSIT_PARTIAL', False)
    world.mock_deposit_inventory_unreadable = _env_bool('MOCK_DEPOSIT_INVENTORY_UNREADABLE', False)
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

    inv = read_inventory(f, inv_roi)
    if inv is None:
        raise PreflightFailed('deposit_inventory_unreadable')

    depot = read_depot_container(f, depot_roi)
    if depot is None:
        raise PreflightFailed('deposit_unreadable_state')

    if not bool(depot.open):
        raise PreflightFailed('deposit_depot_not_open')

    ctx.status.state = RuntimeState.READY
    return capture_mock, input_mock, binding_mock


def run(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    return deposit_preflight(ctx)
