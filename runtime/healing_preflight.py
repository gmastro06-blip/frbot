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
from runtime.healing_semantics import detect_cooldown_marker, parse_rgb_triplet, read_bar_percent, read_percent_with_consistency, read_text_percent


CaptureAdapter: TypeAlias = MssBoundWindowRealCapture | MockWorldAnyCapture
InputAdapter: TypeAlias = Win32HwndKeyboard | MockWorldInput
WindowBindingAdapter: TypeAlias = Win32WindowBinding | MockWindowBinding


def _mock_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw is not None else int(default)
    except Exception:
        return int(default)


def healing_preflight(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    """Healing-only preflight.

    Invariant: if we cannot read HP/MP semantically -> abort.
    """

    ctx.status.state = RuntimeState.PREFLIGHT
    ctx.status.reason = ''

    mode = ctx.config.mode.strip().lower()

    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)

    hp_bar = ctx.rois.get(ctx.config.hp_bar_roi)
    hp_text = ctx.rois.get(ctx.config.hp_text_roi)
    mp_bar = ctx.rois.get(ctx.config.mp_bar_roi)
    mp_text = ctx.rois.get(ctx.config.mp_text_roi)
    cooldown_roi = ctx.rois.get(ctx.config.heal_cooldown_roi)

    if hp_bar is None and hp_text is None:
        raise PreflightFailed('hp_mp_unreadable')
    if mp_bar is None and mp_text is None:
        raise PreflightFailed('hp_mp_unreadable')
    if cooldown_roi is None:
        raise PreflightFailed('heal_cooldown_unknown')

    if mode == 'real':
        binding_real = Win32WindowBinding(
            hwnd=int(ctx.config.window_hwnd),
            title_substring=ctx.config.window_title_substring,
        )
        bvr = binding_real.verify()
        if not bvr.ok:
            raise PreflightFailed('healing_window_binding_lost')

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
            raise PreflightFailed('healing_window_binding_lost')

        # Verify we can read HP/MP and cooldown semantically.
        f = capture_real.grab()
        hp = read_percent_with_consistency(
            bar=(read_bar_percent(f, hp_bar, channel='r') if hp_bar is not None else None),
            text=(read_text_percent(f, hp_text) if hp_text is not None else None),
            tol=float(ctx.config.heal_consistency_tol),
        )
        mp = read_percent_with_consistency(
            bar=(read_bar_percent(f, mp_bar, channel='b') if mp_bar is not None else None),
            text=(read_text_percent(f, mp_text) if mp_text is not None else None),
            tol=float(ctx.config.heal_consistency_tol),
        )
        if hp is None or mp is None:
            raise PreflightFailed('hp_mp_unreadable')

        marker = parse_rgb_triplet(os.environ.get('FRBOT_HEAL_COOLDOWN_RGB', '255,255,0') or '255,255,0', default=(255, 255, 0))
        tol = int(os.environ.get('FRBOT_HEAL_COOLDOWN_TOL', '0') or '0')
        cd = detect_cooldown_marker(f, cooldown_roi, marker_rgb=marker, tol=tol)
        if cd is None:
            raise PreflightFailed('heal_cooldown_unknown')

        ctx.status.state = RuntimeState.READY
        return capture_real, input_real, binding_real

    # mock
    binding_mock = MockWindowBinding()
    bvr = binding_mock.verify()
    if not bvr.ok:
        raise PreflightFailed('healing_window_binding_lost')

    cap_ok = os.environ.get('FRBOT_MOCK_CAPTURE_OK', '1') == '1'
    inp_ok = os.environ.get('FRBOT_MOCK_INPUT_OK', '1') == '1'

    key_kinds = {
        str(ctx.config.heal_key): 'heal',
    }

    mock_ev = (os.environ.get('MOCK_HEAL_EVIDENCE') or os.environ.get('FRBOT_MOCK_HEAL_EVIDENCE') or 'ok')
    mock_cd = (os.environ.get('MOCK_HEAL_COOLDOWN') or os.environ.get('FRBOT_MOCK_HEAL_COOLDOWN') or 'clear')

    world = MockWorld.create(
        rois=ctx.rois,
        key_kinds=key_kinds,
        minimap_noise=False,
        hp_current=_mock_int('FRBOT_MOCK_HP_CURRENT', 40),
        hp_max=_mock_int('FRBOT_MOCK_HP_MAX', 100),
        mp_current=_mock_int('FRBOT_MOCK_MP_CURRENT', 80),
        mp_max=_mock_int('FRBOT_MOCK_MP_MAX', 100),
        heal_amount=_mock_int('FRBOT_MOCK_HEAL_AMOUNT', 30),
        heal_behavior=os.environ.get('FRBOT_MOCK_HEAL_BEHAVIOR', 'normal') or 'normal',
        mock_heal_evidence=str(mock_ev),
        mock_heal_cooldown=str(mock_cd),
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
    hp = read_percent_with_consistency(
        bar=(read_bar_percent(f, hp_bar, channel='r') if hp_bar is not None else None),
        text=(read_text_percent(f, hp_text) if hp_text is not None else None),
        tol=float(ctx.config.heal_consistency_tol),
    )
    mp = read_percent_with_consistency(
        bar=(read_bar_percent(f, mp_bar, channel='b') if mp_bar is not None else None),
        text=(read_text_percent(f, mp_text) if mp_text is not None else None),
        tol=float(ctx.config.heal_consistency_tol),
    )
    if hp is None or mp is None:
        raise PreflightFailed('hp_mp_unreadable')

    marker = parse_rgb_triplet(os.environ.get('FRBOT_HEAL_COOLDOWN_RGB', '255,255,0') or '255,255,0', default=(255, 255, 0))
    tol = int(os.environ.get('FRBOT_HEAL_COOLDOWN_TOL', '0') or '0')
    cd = detect_cooldown_marker(f, cooldown_roi, marker_rgb=marker, tol=tol)
    if cd is None:
        raise PreflightFailed('heal_cooldown_unknown')

    ctx.status.state = RuntimeState.READY
    return capture_mock, input_mock, binding_mock


def run(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    return healing_preflight(ctx)
