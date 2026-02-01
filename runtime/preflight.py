from __future__ import annotations

import os
from typing import TypeAlias

from adapters.capture.mock_world import MockWorldCapture
from adapters.capture.mss_bound_window_real import MssBoundMinimapRealCapture
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
from runtime.minimap_semantics import detect_player_marker, marker_config_from_env

CaptureAdapter: TypeAlias = MssBoundMinimapRealCapture | MockWorldCapture
InputAdapter: TypeAlias = Win32HwndKeyboard | MockWorldInput
WindowBindingAdapter: TypeAlias = Win32WindowBinding | MockWindowBinding


def preflight(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    """Validate environment + adapters + contracts.

    Invariant: if anything is not verifiable -> abort.
    """
    ctx.status.state = RuntimeState.PREFLIGHT
    ctx.status.reason = ''

    mode = ctx.config.mode.strip().lower()

    if mode == 'real':
        # Strong binding is required BEFORE we attempt any capture/input.
        binding_real = Win32WindowBinding(
            hwnd=int(ctx.config.window_hwnd),
            title_substring=ctx.config.window_title_substring,
        )
        bvr = binding_real.verify()
        if not bvr.ok:
            raise PreflightFailed('window_binding_lost')

        snap = binding_real.snapshot()

        loaded = load_rois(ctx)
        ctx.rois = dict(loaded.rois)
        minimap_roi = ctx.rois.get(ctx.config.minimap_roi)
        if minimap_roi is None:
            raise PreflightFailed('minimap_not_detected')

        try:
            capture_real = MssBoundMinimapRealCapture(minimap_roi=minimap_roi, binding=binding_real)
        except ImportError as exc:
            raise PreflightFailed(str(exc)) from exc

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

        # Must be able to see minimap evidence + player marker in real mode.
        try:
            binding_real.assert_bound()
        except Exception:
            raise PreflightFailed('window_binding_lost')
        before = capture_real.grab()
        if not before.minimap_detected:
            raise PreflightFailed('minimap_not_detected')
        cfg = marker_config_from_env(
            ctx.config.player_marker_rgb,
            str(ctx.config.player_marker_tol),
            str(ctx.config.player_marker_min_pixels),
            str(ctx.config.player_marker_max_pixels),
            str(ctx.config.player_marker_min_fill_ratio),
            str(ctx.config.player_marker_max_aspect_ratio),
        )
        if detect_player_marker(before, cfg) is None:
            raise PreflightFailed('minimap_player_not_found')

        ctx.status.state = RuntimeState.READY
        return capture_real, input_real, binding_real

    # mock mode: deterministic adapters. Verification is explicit.
    binding_mock = MockWindowBinding()
    bvr = binding_mock.verify()
    if not bvr.ok:
        raise PreflightFailed('window_binding_lost')

    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)
    if ctx.config.minimap_roi not in ctx.rois:
        raise PreflightFailed('minimap_not_detected')
    cap_ok = os.environ.get('FRBOT_MOCK_CAPTURE_OK', '1') == '1'
    inp_ok = os.environ.get('FRBOT_MOCK_INPUT_OK', '1') == '1'

    key_kinds = {
        # Movement keys.
        'up': 'move_up',
        'down': 'move_down',
        'left': 'move_left',
        'right': 'move_right',
    }
    if os.environ.get('FRBOT_MOCK_STUCK', '0') == '1':
        key_kinds = {'up': 'noop', 'down': 'noop', 'left': 'noop', 'right': 'noop'}
    minimap_noise = os.environ.get('FRBOT_MOCK_MINIMAP_NOISE', '0') == '1'
    world = MockWorld.create(rois=ctx.rois, key_kinds=key_kinds, minimap_noise=minimap_noise)
    world.on_noop()
    capture_mock = MockWorldCapture(world=world, verified=cap_ok)
    input_mock = MockWorldInput(world=world, verified=inp_ok)

    verified_capture = bool(capture_mock.verify().ok)
    verified_input = bool(input_mock.verify().ok)

    ctx.capture = CaptureStatus(backend=capture_mock.name, verified=verified_capture)
    ctx.input = InputStatus(backend=input_mock.name, verified=verified_input)

    if not verified_capture:
        raise PreflightFailed('capture not verified')
    if not verified_input:
        raise PreflightFailed('input not verified')

    # Must be able to see minimap + player marker (semantic evidence).
    before = capture_mock.grab()
    if not before.minimap_detected:
        raise PreflightFailed('minimap_not_detected')
    cfg = marker_config_from_env(
        ctx.config.player_marker_rgb,
        str(ctx.config.player_marker_tol),
        str(ctx.config.player_marker_min_pixels),
        str(ctx.config.player_marker_max_pixels),
        str(ctx.config.player_marker_min_fill_ratio),
        str(ctx.config.player_marker_max_aspect_ratio),
    )
    if detect_player_marker(before, cfg) is None:
        raise PreflightFailed('minimap_player_not_found')

    ctx.status.state = RuntimeState.READY
    return capture_mock, input_mock, binding_mock
