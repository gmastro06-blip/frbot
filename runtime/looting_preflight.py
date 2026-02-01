from __future__ import annotations

import os
from typing import Literal, TypeAlias

from adapters.capture.mock_world_any import MockWorldAnyCapture
from adapters.capture.mss_bound_window_real import MssBoundWindowRealCapture
from adapters.input.mock_world import MockWorldInput
from adapters.input.win32_hwnd import Win32HwndKeyboard
from adapters.mock_world import MockWorld
from adapters.window.mock import MockWindowBinding
from adapters.window.win32 import Win32WindowBinding
from contracts.capture import Frame
from contracts.capture import CaptureStatus
from contracts.errors import PreflightFailed
from contracts.evidence import Roi
from contracts.input import InputStatus
from contracts.runtime import RuntimeContext, RuntimeState
from runtime.config_loader import load_rois
from runtime.inventory_semantics import read_inventory


CaptureAdapter: TypeAlias = MssBoundWindowRealCapture | MockWorldAnyCapture
InputAdapter: TypeAlias = Win32HwndKeyboard | MockWorldInput
WindowBindingAdapter: TypeAlias = Win32WindowBinding | MockWindowBinding


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


def _read_container_open(frame: Frame | None, roi: Roi | None) -> bool | None:
    if frame is None or not getattr(frame, 'rgb', b''):
        return None
    try:
        w = int(getattr(frame, 'width', 0))
        h = int(getattr(frame, 'height', 0))
        if w <= 0 or h <= 0:
            return None
        if roi is None:
            return None
        if int(roi.x) < 0 or int(roi.y) < 0:
            return None
        if (int(roi.x) + int(roi.width)) > w or (int(roi.y) + int(roi.height)) > h:
            return None

        row_stride = w * 3
        out_row_stride = int(roi.width) * 3
        src = frame.rgb
        for row in range(int(roi.height)):
            start = ((int(roi.y) + row) * row_stride) + (int(roi.x) * 3)
            end = start + out_row_stride
            for b in src[start:end]:
                if int(b) != 0:
                    return True
        return False
    except Exception:
        return None


def looting_preflight(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    """Looting gate preflight.

    Invariants:
    - Strong window binding in real mode
    - Required ROIs exist
    - Inventory snapshot is semantically readable
    - (Free mode) container open ROI must be readable (state may start closed)
    """

    ctx.status.state = RuntimeState.PREFLIGHT
    ctx.status.reason = ''

    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)

    mode = ctx.config.mode.strip().lower()
    looting_mode: Literal['premium', 'free'] = 'premium'
    if str(ctx.config.looting_mode).strip().lower() == 'free':
        looting_mode = 'free'

    ctx.looting.mode = looting_mode
    ctx.looting.attempts_used = 0
    ctx.looting.items_looted = 0
    ctx.looting.last_inventory = None
    ctx.looting.container_open = False

    required = [str(ctx.config.inventory_text_roi)]
    if looting_mode == 'free':
        required.extend([str(ctx.config.loot_container_open_roi), str(ctx.config.loot_corpse_roi), str(ctx.config.loot_take_roi)])

    for name in required:
        if not name or ctx.rois.get(name) is None:
            raise PreflightFailed('looting_ambiguous_result')

    if mode == 'real':
        binding_real = Win32WindowBinding(hwnd=int(ctx.config.window_hwnd), title_substring=ctx.config.window_title_substring)
        bvr = binding_real.verify()
        if not bvr.ok:
            raise PreflightFailed('looting_window_binding_lost')

        try:
            capture_real = MssBoundWindowRealCapture(binding=binding_real)
        except ImportError as exc:
            raise PreflightFailed(str(exc)) from exc

        snap = binding_real.snapshot()
        try:
            input_real = Win32HwndKeyboard(hwnd=int(snap.hwnd))
        except Exception as exc:
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
        except Exception:
            raise PreflightFailed('looting_window_binding_lost')

        f = capture_real.grab()

        inv_roi = ctx.rois.get(ctx.config.inventory_text_roi)
        if inv_roi is None or read_inventory(f, inv_roi) is None:
            raise PreflightFailed('looting_inventory_unreadable')

        if looting_mode == 'free':
            open_roi = ctx.rois.get(ctx.config.loot_container_open_roi)
            if open_roi is None:
                raise PreflightFailed('looting_ambiguous_result')
            if _read_container_open(f, open_roi) is None:
                raise PreflightFailed('looting_container_state_unknown')

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
        mock_loot_container_opens=_env_bool('MOCK_LOOT_CONTAINER_OPENS', False),
        mock_loot_inventory_read_fail=_env_bool('MOCK_LOOT_INVENTORY_READ_FAIL', False),
        mock_loot_premium=_env_bool('MOCK_LOOT_PREMIUM', True),
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

    f = capture_mock.grab()
    inv_roi = ctx.rois.get(ctx.config.inventory_text_roi)
    if inv_roi is None or read_inventory(f, inv_roi) is None:
        raise PreflightFailed('looting_inventory_unreadable')

    if looting_mode == 'free':
        open_roi = ctx.rois.get(ctx.config.loot_container_open_roi)
        if open_roi is None:
            raise PreflightFailed('looting_ambiguous_result')
        if _read_container_open(f, open_roi) is None:
            raise PreflightFailed('looting_container_state_unknown')

    ctx.status.state = RuntimeState.READY
    return capture_mock, input_mock, binding_mock


def run(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    return looting_preflight(ctx)
