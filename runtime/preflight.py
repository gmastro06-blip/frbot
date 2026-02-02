from __future__ import annotations

import os
import time
from typing import TypeAlias

from adapters.capture.mock_world import MockWorldCapture
from adapters.capture.cam_real import CamMinimapRealCapture
from adapters.capture.meld_real import MeldBoundMinimapRealCapture
from adapters.capture.meld_projector_real import MeldProjectorMinimapRealCapture
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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return float(default)
    try:
        return float(str(raw).strip())
    except Exception:
        return float(default)


def _maybe_focus_projector_window(*, hwnd: int, title_substring: str) -> None:
    """Best-effort focus to satisfy Win32WindowBinding.verify() foreground requirement.

    Strictness is preserved: verification still fails if foreground cannot be acquired.
    """

    if not _env_bool('FRBOT_PROJECTOR_FOCUS_ON_START', False):
        return

    try:
        from adapters.windows import win32 as w32
    except Exception:
        return

    timeout_s = max(0.0, min(10.0, _env_float('FRBOT_PROJECTOR_FOCUS_TIMEOUT_S', 2.0)))
    deadline = time.monotonic() + float(timeout_s)

    target_hwnd = int(hwnd)
    if target_hwnd <= 0 and str(title_substring or '').strip():
        try:
            match = w32.find_window_by_title_substring(str(title_substring))
            if match is not None:
                target_hwnd = int(match.hwnd)
        except Exception:
            target_hwnd = 0

    if target_hwnd <= 0:
        return

    while True:
        remaining = float(deadline - time.monotonic())
        # Let the win32 helper do a short internal retry loop too.
        per_attempt_timeout = 0.0
        if remaining > 0.0:
            per_attempt_timeout = min(0.5, remaining)
        try:
            w32.try_focus_window(int(target_hwnd), timeout_s=per_attempt_timeout)
        except Exception:
            pass
        try:
            if int(w32.get_foreground_window()) == int(target_hwnd):
                return
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return
        # CI guardrail: no time.sleep outside tick pacing. Best-effort focus may
        # spin briefly until deadline; internal win32 focus helper may already
        # perform bounded waiting.

CaptureAdapter: TypeAlias = (
    MssBoundMinimapRealCapture | MeldBoundMinimapRealCapture | MeldProjectorMinimapRealCapture | CamMinimapRealCapture | MockWorldCapture
)
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
        if os.name != 'nt':
            raise PreflightFailed('unsupported_platform')
        backend = (os.environ.get('FRBOT_CAPTURE_BACKEND', 'mss') or 'mss').strip().lower()

        # Allow projector mode to bind against the OBS Projector window (not Tibia).
        # This keeps the strict foreground invariant, but against the projector HWND/title.
        binding_hwnd = int(ctx.config.window_hwnd)
        binding_title = ctx.config.window_title_substring
        if backend in {'projector', 'meld-projector', 'obs-projector'}:
            raw_hwnd = (os.environ.get('FRBOT_PROJECTOR_WINDOW_HWND', '') or '').strip()
            if raw_hwnd:
                try:
                    binding_hwnd = int(raw_hwnd, 0)
                except Exception:
                    # Keep config value; binding.verify() will surface a usable reason.
                    pass
            raw_title = (os.environ.get('FRBOT_PROJECTOR_WINDOW_TITLE', '') or '').strip()
            if raw_title:
                binding_title = raw_title

        # Strong binding is required BEFORE we attempt any capture/input.
        if backend in {'projector', 'meld-projector', 'obs-projector'}:
            _maybe_focus_projector_window(hwnd=int(binding_hwnd), title_substring=str(binding_title))
        binding_real = Win32WindowBinding(
            hwnd=int(binding_hwnd),
            title_substring=binding_title,
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

        capture_real: MssBoundMinimapRealCapture | MeldBoundMinimapRealCapture | MeldProjectorMinimapRealCapture | CamMinimapRealCapture

        if backend == 'meld':
            try:
                capture_real = MeldBoundMinimapRealCapture(minimap_roi=minimap_roi, binding=binding_real)
            except ImportError as exc:
                raise PreflightFailed('capture_black_or_unavailable') from exc
        elif backend in {'obs-projector', 'projector', 'meld-projector'}:
            try:
                capture_real = MeldProjectorMinimapRealCapture(minimap_roi=minimap_roi, binding=binding_real)
            except ImportError as exc:
                raise PreflightFailed('capture_black_or_unavailable') from exc
        elif backend in {'cam', 'obs', 'virtualcam'}:
            try:
                capture_real = CamMinimapRealCapture(minimap_roi=minimap_roi, binding=binding_real)
            except ImportError as exc:
                raise PreflightFailed('capture_black_or_unavailable') from exc
        elif backend == 'mss':
            try:
                capture_real = MssBoundMinimapRealCapture(minimap_roi=minimap_roi, binding=binding_real)
            except ImportError as exc:
                raise PreflightFailed(str(exc)) from exc
        else:
            raise PreflightFailed('capture_black_or_unavailable')

        try:
            input_real = Win32HwndKeyboard(hwnd=int(snap.hwnd))
        except Exception as exc:
            raise PreflightFailed(f'failed to initialize win32 input: {type(exc).__name__}: {exc}') from exc

        cap_v = capture_real.verify()
        inp_v = input_real.verify()

        ctx.capture = CaptureStatus(backend=capture_real.name, verified=cap_v.ok)
        ctx.input = InputStatus(backend=input_real.name, verified=inp_v.ok)

        if not cap_v.ok:
            if backend == 'meld':
                raise PreflightFailed('capture_black_or_unavailable')
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
