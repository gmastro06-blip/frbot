from __future__ import annotations

import os
from typing import TypeAlias

from adapters.capture.mock_world import MockWorldCapture
from adapters.capture.obs_source_real import ObsSourceRealCapture
from adapters.capture.meld_real import MeldBoundMinimapRealCapture
from adapters.capture.mss_bound_window_real import MssBoundMinimapRealCapture
from adapters.input.mock_world import MockWorldInput
from adapters.input.win32_hwnd import Win32HwndKeyboard
from adapters.mock_world import MockWorld
from adapters.window.mock import MockWindowBinding
from adapters.window.win32 import Win32WindowBinding
from contracts.capture import Frame
from contracts.capture import CaptureStatus
from contracts.errors import PreflightFailed
from contracts.input import InputStatus
from contracts.runtime import RuntimeContext, RuntimeState
from runtime.config_loader import load_rois
from runtime.minimap_semantics import detect_player_marker, marker_config_from_env
from runtime.capture_source import capture_source, resolve_input_hwnd, resolve_obs_projector_hwnd
from runtime.startup_guards import enforce_prod_emergency_real_startup_guards
from runtime.roi_contract import validate_prod_emergency_real_rois_in_bounds


def _frames_dir_for_preflight() -> str:
    raw = (os.environ.get('FRBOT_REAL_FRAMES_DIR', '') or '').strip()
    if raw:
        return raw
    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    if profile == 'prod_emergency':
        return str(os.path.join('diagnostics', 'frames_emergency'))
    return str(os.path.join('diagnostics', 'frames'))


def _dump_preflight_failure_frames(*, reason: str, before: Frame) -> None:
    try:
        from diagnostics.frame_dump import dump_enabled, dump_pair

        if not dump_enabled():
            return
        dump_pair(gate='preflight', before=before, after=None, reason=str(reason), out_dir=_frames_dir_for_preflight())
    except Exception as e:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
        return

CaptureAdapter: TypeAlias = MssBoundMinimapRealCapture | MeldBoundMinimapRealCapture | ObsSourceRealCapture | MockWorldCapture
InputAdapter: TypeAlias = Win32HwndKeyboard | MockWorldInput
WindowBindingAdapter: TypeAlias = Win32WindowBinding | MockWindowBinding


def preflight(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    """Validate environment + adapters + contracts.

    Invariant: if anything is not verifiable -> abort.
    """
    ctx.status.state = RuntimeState.PREFLIGHT
    ctx.status.reason = ''

    mode = ctx.config.mode.strip().lower()

    # PROD-EMERGENCY: require explicit HWND-bound foreground before doing anything else.
    if mode == 'real':
        enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=False)

    if mode == 'real':
        if os.name != 'nt':
            raise PreflightFailed('unsupported_platform')
        backend = (os.environ.get('FRBOT_CAPTURE_BACKEND', 'mss') or 'mss').strip().lower()

        cap_source = capture_source()

        # PROD-EMERGENCY: REAL capture is HWND-bound only (MSS/DXGI). No projector/cam.
        profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        if profile == 'prod_emergency' and backend not in {'mss', 'meld'}:
            raise PreflightFailed('capture_invalid')

        # InputAuthority (strict): always bind to Tibia HWND/title; capture is separate.
        binding_hwnd = int(ctx.config.window_hwnd)
        binding_title = ctx.config.window_title_substring
        binding_real = Win32WindowBinding(hwnd=int(binding_hwnd), title_substring=binding_title)
        bvr = binding_real.verify()
        if not bvr.ok:
            raise PreflightFailed('window_binding_lost')

        # Input always targets the game client HWND (PostMessage). In OBS capture mode,
        # this is intentionally decoupled from the capture binding.
        input_hwnd = resolve_input_hwnd(hwnd=int(ctx.config.window_hwnd), title_substring=ctx.config.window_title_substring)
        if input_hwnd <= 0:
            raise PreflightFailed('window_binding_lost')

        loaded = load_rois(ctx)
        ctx.rois = dict(loaded.rois)
        minimap_roi = ctx.rois.get(ctx.config.minimap_roi)
        if minimap_roi is None:
            raise PreflightFailed('minimap_not_detected')

        capture_real: MssBoundMinimapRealCapture | MeldBoundMinimapRealCapture | ObsSourceRealCapture

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
            # Capture binding: either client HWND (default) or OBS Projector HWND.
            cap_binding = binding_real
            if cap_source == 'obs':
                obs_hwnd, _obs_title = resolve_obs_projector_hwnd()
                cap_binding = Win32WindowBinding(hwnd=int(obs_hwnd), title_substring=os.environ.get('FRBOT_OBS_PROJECTOR_TITLE', '') or '')

            if backend == 'meld':
                try:
                    capture_real = MeldBoundMinimapRealCapture(minimap_roi=minimap_roi, binding=cap_binding)
                except ImportError as exc:
                    raise PreflightFailed('capture_black_or_unavailable') from exc
            elif backend == 'mss':
                try:
                    capture_real = MssBoundMinimapRealCapture(minimap_roi=minimap_roi, binding=cap_binding)
                except ImportError as exc:
                    raise PreflightFailed(str(exc)) from exc
            else:
                raise PreflightFailed('capture_black_or_unavailable')

        try:
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

        # PROD-EMERGENCY: strict ROI contract must be in-bounds against the real capture.
        validate_prod_emergency_real_rois_in_bounds(rois=ctx.rois, frame=before)

        if not before.minimap_detected:
            _dump_preflight_failure_frames(reason='minimap_not_detected', before=before)
            raise PreflightFailed('minimap_not_detected')
        cfg = marker_config_from_env(
            ctx.config.player_marker_rgb,
            str(ctx.config.player_marker_tol),
            str(ctx.config.player_marker_min_pixels),
            str(ctx.config.player_marker_max_pixels),
            str(ctx.config.player_marker_min_fill_ratio),
            str(ctx.config.player_marker_max_aspect_ratio),
        )
        det = detect_player_marker(before, cfg)
        if det is None:
            # Fallback for REAL runs: marker colors differ by client/theme.
            # Keep deterministic: only try a small, ordered set.
            candidates: list[str] = []
            for rgb in [ctx.config.player_marker_rgb, '255,255,0', '255,0,255', '255,255,255']:
                s = str(rgb or '').strip()
                if s and s not in candidates:
                    candidates.append(s)

            chosen: str | None = None
            chosen_min_pixels: int | None = None
            best_score: float = -1.0
            base_min_pixels = int(ctx.config.player_marker_min_pixels)
            # Deterministic, conservative relaxation: allow as low as 3 pixels.
            min_pixel_options = [base_min_pixels]
            if base_min_pixels > 3:
                min_pixel_options.append(3)

            for rgb in candidates:
                for min_pix in min_pixel_options:
                    cfg2 = marker_config_from_env(
                        rgb,
                        str(ctx.config.player_marker_tol),
                        str(min_pix),
                        str(ctx.config.player_marker_max_pixels),
                        str(ctx.config.player_marker_min_fill_ratio),
                        str(ctx.config.player_marker_max_aspect_ratio),
                    )
                    det2 = detect_player_marker(before, cfg2)
                    if det2 is None:
                        continue
                    # Prefer larger, denser, more compact detections.
                    score = float(det2.pixel_count) * float(det2.fill_ratio) / max(1.0, float(det2.aspect_ratio))
                    if score > best_score:
                        best_score = score
                        chosen = rgb
                        chosen_min_pixels = int(min_pix)

            if chosen is None:
                _dump_preflight_failure_frames(reason='minimap_player_not_found', before=before)
                raise PreflightFailed('minimap_player_not_found')
            os.environ['FRBOT_PLAYER_MARKER_RGB_EFFECTIVE'] = str(chosen)
            if chosen_min_pixels is not None:
                os.environ['FRBOT_PLAYER_MARKER_MIN_PIXELS_EFFECTIVE'] = str(int(chosen_min_pixels))

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

        # WASD movement keys (dual support).
        'w': 'move_up',
        's': 'move_down',
        'a': 'move_left',
        'd': 'move_right',
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
