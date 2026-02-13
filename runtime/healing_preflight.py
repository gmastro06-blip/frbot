from __future__ import annotations

import os
from typing import TypeAlias

from adapters.capture.mock_world_any import MockWorldAnyCapture
from adapters.capture.mss_bound_window_real import MssBoundWindowRealCapture
from adapters.capture.meld_real import MeldBoundWindowRealCapture
from adapters.capture.obs_source_real import ObsSourceRealCapture
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
from runtime.healing_semantics import (
    detect_cooldown_marker,
    parse_rgb_triplet,
    read_bar_percent,
    read_hp_mp_text_pair,
    read_percent_with_consistency,
    read_text_percent,
)
from runtime.startup_guards import enforce_prod_emergency_real_startup_guards
from runtime.capture_source import capture_source, resolve_input_hwnd, resolve_obs_projector_hwnd
from runtime.roi_contract import validate_prod_emergency_real_rois_in_bounds


CaptureAdapter: TypeAlias = MssBoundWindowRealCapture | MeldBoundWindowRealCapture | ObsSourceRealCapture | MockWorldAnyCapture
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

    if mode == 'real':
        enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=False)

    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)

    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()

    hp_mp_roi = ctx.rois.get(getattr(ctx.config, 'hp_mp_roi', 'hp_mp'))
    hp_bar = ctx.rois.get(ctx.config.hp_bar_roi)
    hp_text = ctx.rois.get(ctx.config.hp_text_roi)
    mp_bar = ctx.rois.get(ctx.config.mp_bar_roi)
    mp_text = ctx.rois.get(ctx.config.mp_text_roi)
    cooldown_roi = ctx.rois.get(ctx.config.heal_cooldown_roi)

    if profile == 'prod_emergency' and mode == 'real':
        # PROD-EMERGENCY REAL contract: we only require hp_mp ROI.
        if hp_mp_roi is None:
            raise PreflightFailed('config_invalid_schema')
    else:
        if hp_bar is None and hp_text is None:
            raise PreflightFailed('hp_mp_unreadable')
        if mp_bar is None and mp_text is None:
            raise PreflightFailed('hp_mp_unreadable')
        if cooldown_roi is None:
            raise PreflightFailed('heal_cooldown_unknown')

    if mode == 'real':
        backend = (os.environ.get('FRBOT_CAPTURE_BACKEND', 'mss') or 'mss').strip().lower()
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
            raise PreflightFailed('healing_window_binding_lost')

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
                raise PreflightFailed('healing_window_binding_lost')
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
                raise PreflightFailed('capture_invalid')
            raise PreflightFailed(cap_v.reason or 'capture not verified')
        if not inp_v.ok:
            raise PreflightFailed(inp_v.reason or 'input not verified')

        try:
            binding_real.assert_bound()
        except Exception:
            raise PreflightFailed('healing_window_binding_lost')

        # Verify we can read HP/MP and cooldown semantically.
        f = capture_real.grab()

        validate_prod_emergency_real_rois_in_bounds(rois=ctx.rois, frame=f)

        if profile == 'prod_emergency' and hp_mp_roi is not None:
            pair = read_hp_mp_text_pair(f, hp_mp_roi)
            if pair is None:
                raise PreflightFailed('hp_mp_unreadable')
        else:
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

            assert cooldown_roi is not None

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

    assert cooldown_roi is not None

    marker = parse_rgb_triplet(os.environ.get('FRBOT_HEAL_COOLDOWN_RGB', '255,255,0') or '255,255,0', default=(255, 255, 0))
    tol = int(os.environ.get('FRBOT_HEAL_COOLDOWN_TOL', '0') or '0')
    cd = detect_cooldown_marker(f, cooldown_roi, marker_rgb=marker, tol=tol)
    if cd is None:
        raise PreflightFailed('heal_cooldown_unknown')

    ctx.status.state = RuntimeState.READY
    return capture_mock, input_mock, binding_mock


def run(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    return healing_preflight(ctx)
