from __future__ import annotations

import json
import os
from typing import TypeAlias

from adapters.capture.mock_world import MockWorldCapture
from adapters.capture.meld_real import MeldBoundMinimapRealCapture
from adapters.capture.mss_bound_window_real import MssBoundMinimapRealCapture
from adapters.input.mock_world import MockWorldInput
from adapters.input.win32_hwnd import Win32HwndKeyboard
from adapters.mock_world import MockWorld
from adapters.window.mock import MockWindowBinding
from adapters.window.win32 import Win32WindowBinding
from contracts.capture import CaptureStatus
from contracts.errors import PreflightFailed
from contracts.input import InputStatus
from typing import Literal

from contracts.runtime import RuntimeContext, RuntimeState, Waypoint
from runtime.cavebot_semantics import detect_player_marker
from runtime.config_loader import load_rois
from runtime.roi_contract import validate_prod_emergency_real_rois_in_bounds
from runtime.startup_guards import enforce_prod_emergency_real_startup_guards
from runtime.capture_source import capture_source, resolve_input_hwnd, resolve_obs_projector_hwnd


CaptureAdapter: TypeAlias = MssBoundMinimapRealCapture | MeldBoundMinimapRealCapture | MockWorldCapture
InputAdapter: TypeAlias = Win32HwndKeyboard | MockWorldInput
WindowBindingAdapter: TypeAlias = Win32WindowBinding | MockWindowBinding


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


def _parse_rgb(raw: str) -> tuple[int, int, int]:
    s = (raw or '').strip()
    parts = [p.strip() for p in s.split(',') if p.strip()]
    if len(parts) != 3:
        return (255, 0, 255)
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return (255, 0, 255)


def _dir(raw: str) -> Literal['N', 'S', 'E', 'W']:
    s = (raw or '').strip().upper()
    if s == 'N':
        return 'N'
    if s == 'S':
        return 'S'
    if s == 'W':
        return 'W'
    return 'E'


def _load_waypoints_from_env(ctx: RuntimeContext) -> tuple[Waypoint, ...]:
    raw = (os.environ.get('FRBOT_CAVEBOT_WAYPOINTS', '') or '').strip()
    if not raw:
        # Explicitly refuse to operate without a declared route.
        raise PreflightFailed('cavebot_waypoint_stuck')

    # Prefer JSON for explicitness.
    if raw.lstrip().startswith('['):
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise PreflightFailed(f'cavebot_waypoint_stuck invalid_waypoints_json: {exc}') from exc

        wps: list[Waypoint] = []
        for idx, item in enumerate(list(data)):
            if not isinstance(item, dict):
                raise PreflightFailed('cavebot_waypoint_stuck')
            wid = str(item.get('waypoint_id', str(idx)))
            x = int(item.get('x', 0))
            y = int(item.get('y', 0))
            z = int(item.get('z', 7))
            radius_px = int(item.get('radius_px', 0))
            max_ticks = int(item.get('max_ticks', 0))
            if radius_px < 0 or max_ticks <= 0:
                raise PreflightFailed('cavebot_waypoint_stuck')
            wps.append(
                Waypoint(
                    waypoint_id=wid,
                    x=x,
                    y=y,
                    z=z,
                    radius_px=int(radius_px),
                    max_ticks=int(max_ticks),
                )
            )
        return tuple(wps)

    # Fallback: semicolon-separated: "x,y,z,radius_px,max_ticks" entries.
    items = [p.strip() for p in raw.split(';') if p.strip()]
    wps2: list[Waypoint] = []
    for idx, item in enumerate(items):
        bits = [b.strip() for b in item.split(',')]
        if len(bits) < 5:
            raise PreflightFailed('cavebot_waypoint_stuck')
        x = int(bits[0])
        y = int(bits[1])
        z = int(bits[2])
        radius_px = int(bits[3])
        max_ticks = int(bits[4])
        if radius_px < 0 or max_ticks <= 0:
            raise PreflightFailed('cavebot_waypoint_stuck')
        wps2.append(
            Waypoint(
                waypoint_id=str(idx),
                x=x,
                y=y,
                z=z,
                radius_px=int(radius_px),
                max_ticks=int(max_ticks),
            )
        )
    return tuple(wps2)


def cavebot_preflight(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    """Cavebot gate preflight.

    Invariants:
    - Strong window binding (foreground + rect) must be valid
    - Minimap ROI must exist
    - Player marker must be detectable (semantic)
    - Waypoints must be explicitly provided
    """

    ctx.status.state = RuntimeState.PREFLIGHT
    ctx.status.reason = ''

    loaded = load_rois(ctx)
    ctx.rois = dict(loaded.rois)

    minimap_roi = ctx.rois.get(ctx.config.minimap_roi)
    if minimap_roi is None:
        raise PreflightFailed('cavebot_marker_not_found')

    # Load waypoint route.
    ctx.cavebot.gate_waypoints = _load_waypoints_from_env(ctx)
    ctx.cavebot.gate_waypoint_index = 0
    ctx.cavebot.gate_attempts_used = 0
    ctx.cavebot.gate_ticks_in_waypoint = 0
    ctx.cavebot.gate_inputs_sent = 0
    ctx.cavebot.gate_reach_streak = 0
    ctx.cavebot_gate.telemetry = type(ctx.cavebot_gate.telemetry)()

    mode = ctx.config.mode.strip().lower()

    if mode == 'real':
        enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=False)

        backend = (os.environ.get('FRBOT_CAPTURE_BACKEND', 'mss') or 'mss').strip().lower()
        profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        if profile == 'prod_emergency' and backend not in {'mss', 'meld'}:
            raise PreflightFailed('capture_invalid')

        if capture_source() == 'obs':
            obs_hwnd, _obs_title = resolve_obs_projector_hwnd()
            binding_real = Win32WindowBinding(hwnd=int(obs_hwnd), title_substring=(os.environ.get('FRBOT_OBS_PROJECTOR_TITLE', '') or ''))
        else:
            binding_real = Win32WindowBinding(hwnd=int(ctx.config.window_hwnd), title_substring=ctx.config.window_title_substring)
        bvr = binding_real.verify()
        if not bvr.ok:
            raise PreflightFailed('cavebot_window_binding_lost')

        capture_real: MssBoundMinimapRealCapture | MeldBoundMinimapRealCapture
        if backend == 'meld':
            try:
                capture_real = MeldBoundMinimapRealCapture(minimap_roi=minimap_roi, binding=binding_real)
            except ImportError as exc:
                raise PreflightFailed('capture_black_or_unavailable') from exc
        elif backend == 'mss':
            try:
                capture_real = MssBoundMinimapRealCapture(minimap_roi, binding=binding_real)
            except ImportError as exc:
                raise PreflightFailed(str(exc)) from exc
        else:
            raise PreflightFailed('capture_black_or_unavailable')

        try:
            input_hwnd = resolve_input_hwnd(hwnd=int(ctx.config.window_hwnd), title_substring=ctx.config.window_title_substring)
            if input_hwnd <= 0:
                raise PreflightFailed('cavebot_window_binding_lost')
            input_real = Win32HwndKeyboard(hwnd=int(input_hwnd))
        except Exception as exc:
            raise PreflightFailed(f'failed to initialize win32 input: {type(exc).__name__}: {exc}') from exc

        cap_v = capture_real.verify()
        inp_v = input_real.verify()
        ctx.capture = CaptureStatus(backend=capture_real.name, verified=cap_v.ok)
        ctx.input = InputStatus(backend=input_real.name, verified=inp_v.ok)

        if not cap_v.ok:
            if profile == 'prod_emergency':
                if capture_source() == 'obs' and (cap_v.reason or '') in {'captured_frame_black', 'capture_black_or_unavailable', 'frame_empty'}:
                    raise PreflightFailed('captured_frame_black_obs')
                raise PreflightFailed('capture_invalid')
            raise PreflightFailed(cap_v.reason or 'capture not verified')
        if not inp_v.ok:
            raise PreflightFailed(inp_v.reason or 'input not verified')

        try:
            binding_real.assert_bound()
        except Exception:
            raise PreflightFailed('cavebot_window_binding_lost')

        f = capture_real.grab()

        # PROD-EMERGENCY: strict ROI contract must be in-bounds against the real capture.
        validate_prod_emergency_real_rois_in_bounds(rois=ctx.rois, frame=f)

        marker = detect_player_marker(
            f,
            marker_rgb=_parse_rgb(ctx.config.player_marker_rgb),
            tol=int(ctx.config.player_marker_tol),
            min_pixels=int(ctx.config.player_marker_min_pixels),
            max_pixels=int(ctx.config.player_marker_max_pixels),
        )
        if marker is None:
            raise PreflightFailed('cavebot_marker_not_found')

        ctx.status.state = RuntimeState.READY
        return capture_real, input_real, binding_real

    # mock
    binding_mock = MockWindowBinding()
    bvr = binding_mock.verify()
    if not bvr.ok:
        raise PreflightFailed('cavebot_window_binding_lost')

    cap_ok = os.environ.get('FRBOT_MOCK_CAPTURE_OK', '1') == '1'
    inp_ok = os.environ.get('FRBOT_MOCK_INPUT_OK', '1') == '1'

    key_kinds = {
        'UP': 'move_up',
        'DOWN': 'move_down',
        'LEFT': 'move_left',
        'RIGHT': 'move_right',
    }

    # Cavebot mock flags.
    marker_static = _env_bool('MOCK_CAVEBOT_MARKER_STATIC', False)
    wrong_dir = _env_bool('MOCK_CAVEBOT_MARKER_WRONG_DIRECTION', False)
    noise_only = _env_bool('MOCK_CAVEBOT_NOISE_ONLY', False)
    progress_ok = _env_bool('MOCK_CAVEBOT_PROGRESS_OK', False)

    world = MockWorld.create(
        rois=ctx.rois,
        key_kinds=key_kinds,
        minimap_noise=bool(noise_only),
        battle_list_rows=(),
        battle_list_selected_row=None,
        battle_list_row_height=16,
        click_behavior='normal',
        mock_cavebot_marker_static=bool(marker_static),
        mock_cavebot_marker_wrong_direction=bool(wrong_dir),
        mock_cavebot_noise_only=bool(noise_only),
        mock_cavebot_progress_ok=bool(progress_ok),
    )
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

    f = capture_mock.grab()
    marker = detect_player_marker(
        f,
        marker_rgb=_parse_rgb(ctx.config.player_marker_rgb),
        tol=int(ctx.config.player_marker_tol),
        min_pixels=int(ctx.config.player_marker_min_pixels),
        max_pixels=int(ctx.config.player_marker_max_pixels),
    )
    if marker is None:
        raise PreflightFailed('cavebot_marker_not_found')

    ctx.status.state = RuntimeState.READY
    return capture_mock, input_mock, binding_mock


def run(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    return cavebot_preflight(ctx)
