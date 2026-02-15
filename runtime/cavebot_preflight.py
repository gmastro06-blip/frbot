from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypeAlias

from adapters.capture.mock_world import MockWorldCapture
from adapters.capture.meld_real import MeldBoundMinimapRealCapture
from adapters.capture.mss_bound_window_real import MssBoundMinimapRealCapture
from adapters.capture.obs_source_real import ObsSourceRealCapture
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
from runtime.cavebot_semantics import detect_player_marker, select_player_marker
from runtime.config_loader import load_rois
from runtime.roi_contract import validate_prod_emergency_real_rois_in_bounds
from runtime.startup_guards import enforce_prod_emergency_real_startup_guards
from runtime.capture_source import capture_source, resolve_input_hwnd, resolve_obs_projector_hwnd


CaptureAdapter: TypeAlias = MssBoundMinimapRealCapture | MeldBoundMinimapRealCapture | ObsSourceRealCapture | MockWorldCapture
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


def _parse_rgb_list(raw: str) -> list[tuple[int, int, int]]:
    items = [p.strip() for p in (raw or '').split(';') if p.strip()]
    out: list[tuple[int, int, int]] = []
    for item in items:
        if item.lower().strip() == 'auto':
            continue
        out.append(_parse_rgb(item))
    return out


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
        raw_path = (os.environ.get('FRBOT_CAVEBOT_WAYPOINTS_FILE', '') or '').strip()
        if raw_path:
            p = Path(raw_path)
            if not p.exists() or not p.is_file():
                raise PreflightFailed('cavebot_waypoint_stuck')
            try:
                raw = (p.read_text(encoding='utf-8', errors='replace') or '').strip()
            except Exception as exc:
                raise PreflightFailed(f'cavebot_waypoint_stuck invalid_waypoints_file: {exc}') from exc
        if not raw:
            # Caller decides whether missing waypoints is fatal.
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
            wp_type = str(item.get('waypoint_type', item.get('type', 'walk')) or 'walk').strip().lower()
            raw_options = item.get('options', {})
            options = dict(raw_options) if isinstance(raw_options, dict) else {}
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
                    waypoint_type=str(wp_type),
                    options=options,
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
                waypoint_type='walk',
                options={},
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

    # Cavebot state is initialized after capture + marker verification.

    mode = ctx.config.mode.strip().lower()

    if mode == 'real':
        enforce_prod_emergency_real_startup_guards(write_fatal_on_fail=False)

        backend = (os.environ.get('FRBOT_CAPTURE_BACKEND', 'mss') or 'mss').strip().lower()
        profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        if profile == 'prod_emergency' and backend not in {'mss', 'meld'}:
            raise PreflightFailed('capture_invalid')

        cap_source = capture_source()

        # InputAuthority (strict): always bind to Tibia HWND/title; capture is separate.
        binding_real = Win32WindowBinding(hwnd=int(ctx.config.window_hwnd), title_substring=ctx.config.window_title_substring)
        bvr = binding_real.verify()
        if not bvr.ok:
            raise PreflightFailed('cavebot_window_binding_lost')

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
            cap_binding = binding_real
            if cap_source == 'obs':
                obs_hwnd, _obs_title = resolve_obs_projector_hwnd()
                cap_binding = Win32WindowBinding(hwnd=int(obs_hwnd), title_substring=(os.environ.get('FRBOT_OBS_PROJECTOR_TITLE', '') or ''))

            if backend == 'meld':
                try:
                    capture_real = MeldBoundMinimapRealCapture(minimap_roi=minimap_roi, binding=cap_binding)
                except ImportError as exc:
                    raise PreflightFailed('capture_black_or_unavailable') from exc
            elif backend == 'mss':
                try:
                    capture_real = MssBoundMinimapRealCapture(minimap_roi, binding=cap_binding)
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
                if cap_source == 'obs' and (cap_v.reason or '') in {'captured_frame_black', 'capture_black_or_unavailable', 'frame_empty'}:
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

        # Marker selection: try configured RGB first, then fallbacks.
        configured_rgb = _parse_rgb(ctx.config.player_marker_rgb)
        fallback_raw = (os.environ.get('FRBOT_PLAYER_MARKER_RGB_FALLBACKS', '') or '').strip()
        fallback_list = _parse_rgb_list(fallback_raw)
        if not fallback_list:
            # Common marker hues to try when the configured value doesn't match.
            fallback_list = [(255, 255, 255), (255, 255, 0), (0, 255, 0), (255, 0, 0)]

        tried = [configured_rgb] + [c for c in fallback_list if c != configured_rgb]

        marker = None
        chosen_rgb: tuple[int, int, int] | None = None
        last_details: dict[str, object] | None = None
        for rgb in tried:
            sel = select_player_marker(
                f,
                marker_rgb=rgb,
                tol=int(ctx.config.player_marker_tol),
                min_pixels=int(ctx.config.player_marker_min_pixels),
                max_pixels=int(ctx.config.player_marker_max_pixels),
                prev_marker=None,
            )
            last_details = dict(sel.details or {})
            if sel.abort_reason is None and sel.marker is not None:
                marker = sel.marker
                chosen_rgb = rgb
                break

        if marker is None:
            err = PreflightFailed('cavebot_marker_not_found')
            setattr(
                err,
                'details',
                {
                    'reason': 'cavebot_marker_not_found',
                    'configured_rgb': list(configured_rgb),
                    'tried_rgbs': [list(t) for t in tried],
                    'hint': 'Set FRBOT_PLAYER_MARKER_RGB or FRBOT_PLAYER_MARKER_RGB_FALLBACKS to match your minimap player marker color',
                    **(last_details or {}),
                },
            )
            raise err

        # Persist chosen marker RGB for subsequent ticks.
        if chosen_rgb is not None:
            ctx.cavebot_gate.telemetry.marker_rgb = chosen_rgb

        # Initialize virtual marker position for scroll-based progress inference.
        try:
            ctx.cavebot_gate.telemetry.virtual_x_px = int(getattr(marker, 'x_px', 0))
            ctx.cavebot_gate.telemetry.virtual_y_px = int(getattr(marker, 'y_px', 0))
        except Exception:
            ctx.cavebot_gate.telemetry.virtual_x_px = None
            ctx.cavebot_gate.telemetry.virtual_y_px = None

        # Load waypoint route.
        raw_wps = (os.environ.get('FRBOT_CAVEBOT_WAYPOINTS', '') or '').strip()
        auto_raw = os.environ.get('FRBOT_CAVEBOT_AUTO_ROUTE')
        profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        auto_enabled = (
            (str(auto_raw).strip().lower() in {'1', 'true', 'yes', 'on'})
            if auto_raw is not None
            else (profile == 'prod_full')
        )

        if not raw_wps:
            if not auto_enabled:
                # Explicitly refuse to operate without a declared route.
                raise PreflightFailed('cavebot_waypoint_stuck')

            # PROD-FULL default: generate a tiny, local route so the gate is runnable
            # without bespoke environment configuration.
            dx = int(os.environ.get('FRBOT_CAVEBOT_AUTO_ROUTE_DX_PX', '8') or '8')
            dy = int(os.environ.get('FRBOT_CAVEBOT_AUTO_ROUTE_DY_PX', '0') or '0')
            radius_px = int(os.environ.get('FRBOT_CAVEBOT_AUTO_ROUTE_RADIUS_PX', '2') or '2')
            max_ticks = int(os.environ.get('FRBOT_CAVEBOT_AUTO_ROUTE_MAX_TICKS', '30') or '30')

            w = int(getattr(f, 'minimap_width', 0) or 0)
            h = int(getattr(f, 'minimap_height', 0) or 0)
            mx = int(getattr(marker, 'x_px', 0))
            my = int(getattr(marker, 'y_px', 0))

            tx = int(mx + dx)
            ty = int(my + dy)
            if w > 0:
                tx = max(0, min(int(w - 1), int(tx)))
            if h > 0:
                ty = max(0, min(int(h - 1), int(ty)))

            if int(tx) == int(mx) and int(ty) == int(my):
                # Ensure the route requests some movement.
                tx = int(mx + 1)
                if w > 0:
                    tx = max(0, min(int(w - 1), int(tx)))

            ctx.cavebot.gate_waypoints = (
                Waypoint(
                    waypoint_id='auto_wp0',
                    x=int(tx),
                    y=int(ty),
                    z=7,
                    radius_px=int(max(0, radius_px)),
                    max_ticks=int(max(1, max_ticks)),
                    waypoint_type='walk',
                    options={},
                ),
            )
        else:
            ctx.cavebot.gate_waypoints = _load_waypoints_from_env(ctx)

        ctx.cavebot.gate_waypoint_index = 0
        ctx.cavebot.gate_attempts_used = 0
        ctx.cavebot.gate_ticks_in_waypoint = 0
        ctx.cavebot.gate_inputs_sent = 0
        ctx.cavebot.gate_reach_streak = 0
        saved_rgb = getattr(ctx.cavebot_gate.telemetry, 'marker_rgb', None)
        ctx.cavebot_gate.telemetry = type(ctx.cavebot_gate.telemetry)()
        if isinstance(saved_rgb, (tuple, list)) and len(saved_rgb) == 3:
            ctx.cavebot_gate.telemetry.marker_rgb = (int(saved_rgb[0]), int(saved_rgb[1]), int(saved_rgb[2]))

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
    dual_marker = _env_bool('MOCK_CAVEBOT_DUAL_MARKER', False)
    minimap_force_black = _env_bool('MOCK_CAVEBOT_MINIMAP_FORCE_BLACK', False)

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
        mock_cavebot_dual_marker=bool(dual_marker),
        mock_cavebot_minimap_force_black=bool(minimap_force_black),
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
    sel = select_player_marker(
        f,
        marker_rgb=_parse_rgb(ctx.config.player_marker_rgb),
        tol=int(ctx.config.player_marker_tol),
        min_pixels=int(ctx.config.player_marker_min_pixels),
        max_pixels=int(ctx.config.player_marker_max_pixels),
        prev_marker=None,
    )
    if sel.abort_reason is not None:
        pf = PreflightFailed(str(sel.abort_reason))
        setattr(pf, 'details', sel.details)
        raise pf
    if sel.marker is None:
        raise PreflightFailed('cavebot_marker_not_found')

    # Waypoints (mock): keep existing strict requirement to preserve test determinism.
    ctx.cavebot.gate_waypoints = _load_waypoints_from_env(ctx)
    ctx.cavebot.gate_waypoint_index = 0
    ctx.cavebot.gate_attempts_used = 0
    ctx.cavebot.gate_ticks_in_waypoint = 0
    ctx.cavebot.gate_inputs_sent = 0
    ctx.cavebot.gate_reach_streak = 0
    ctx.cavebot_gate.telemetry = type(ctx.cavebot_gate.telemetry)()

    ctx.status.state = RuntimeState.READY
    return capture_mock, input_mock, binding_mock


def run(ctx: RuntimeContext) -> tuple[CaptureAdapter, InputAdapter, WindowBindingAdapter]:
    return cavebot_preflight(ctx)
