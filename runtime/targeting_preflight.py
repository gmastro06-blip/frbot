from __future__ import annotations

import os
from typing import TypeAlias

from adapters.capture.mock_world_any import MockWorldAnyCapture
from adapters.capture.mss_bound_window_real import MssBoundWindowRealCapture
from adapters.input.mock_world import MockWorldInput
from adapters.input.win32_hwnd import Win32HwndKeyboard
from adapters.mock_world import MockBattleListRow, MockWorld
from adapters.window.mock import MockWindowBinding
from adapters.window.win32 import Win32WindowBinding
from contracts.capture import CaptureStatus
from contracts.errors import PreflightFailed
from contracts.input import InputStatus
from contracts.runtime import RuntimeContext, RuntimeState
from runtime.battle_list_semantics import detect_battle_list
from runtime.config_loader import load_rois


CaptureAdapter: TypeAlias = MssBoundWindowRealCapture | MockWorldAnyCapture
InputAdapter: TypeAlias = Win32HwndKeyboard | MockWorldInput
WindowBindingAdapter: TypeAlias = Win32WindowBinding | MockWindowBinding


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

    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)

    battle_roi = ctx.rois.get(ctx.config.battle_list_roi)
    if battle_roi is None:
        raise PreflightFailed('battle_list_not_detected')

    if mode == 'real':
        binding_real = Win32WindowBinding(
            hwnd=int(ctx.config.window_hwnd),
            title_substring=ctx.config.window_title_substring,
        )
        bvr = binding_real.verify()
        if not bvr.ok:
            raise PreflightFailed('targeting_window_binding_lost')

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
            raise PreflightFailed('targeting_window_binding_lost')

        before = capture_real.grab()
        # Battle List bbox must be within captured HWND region.
        if (battle_roi.x + battle_roi.width) > before.width or (battle_roi.y + battle_roi.height) > before.height:
            raise PreflightFailed('targeting_window_binding_lost')

        obs = detect_battle_list(before, battle_roi)
        if obs is None:
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
    if (battle_roi.x + battle_roi.width) > before.width or (battle_roi.y + battle_roi.height) > before.height:
        raise PreflightFailed('targeting_window_binding_lost')

    obs = detect_battle_list(before, battle_roi)
    if obs is None:
        raise PreflightFailed('battle_list_not_detected')

    ctx.status.state = RuntimeState.READY
    return capture_mock, input_mock, binding_mock


def run(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    return targeting_preflight(ctx)
