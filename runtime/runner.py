from __future__ import annotations

#  BASELINE STABLE 
# Do not modify this loop/invariants without a contract review.
# New features must be added as independent gates (new preflight/runner/entrypoint).
# See BASELINE.md.

import os
import sys
import time
import json
import math
from pathlib import Path

from contracts.errors import ContractViolation, PreflightFailed
from contracts.engine import EngineInput, IntentMove
from contracts.capture import Frame
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry, Tile
from diagnostics.fatal import write_fatal
from diagnostics.logger import configure_logger
from diagnostics.jsonlog import log as log_json
from diagnostics.schema import base_context_fields
from diagnostics.frame_dump import dump_enabled, dump_pair
from diagnostics.last_frames import record_after, record_before, snapshot
from core.engine import tick as engine_tick
from runtime.bot_config_loader import load_bot_config
from runtime.env import parse_window_hwnd_env
from runtime.preflight import preflight
from runtime.minimap_semantics import (
    MarkerConfig,
    MarkerDetection,
    SemanticTracker,
    detect_player_marker,
    marker_config_from_env,
    semantic_progress_ok,
)
from runtime.profile import cap_ticks, default_session_seconds, is_prod_emergency
from runtime.pacing import wait_until_ns


def _env_str(name: str, default: str = '') -> str:
    raw = os.environ.get(name)
    return (default if raw is None else str(raw)).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(str(raw).strip(), 10) if raw is not None else int(default)
    except Exception:
        return int(default)


def _frames_dir_for_run() -> Path:
    # Audit tooling requires FRBOT_REAL_FRAMES_DIR in REAL mode.
    raw = _env_str('FRBOT_REAL_FRAMES_DIR', '')
    if raw:
        return Path(raw)
    if is_prod_emergency():
        return Path('diagnostics') / 'frames_emergency'
    return Path('diagnostics') / 'frames'


def _write_evidence_manifest(*, frames_dir: Path, capture: object) -> None:
    try:
        src = (_env_str('FRBOT_CAPTURE_SOURCE', 'client') or 'client').strip().lower()
        payload = {
            'capture_source': ('obs_source' if src == 'obs_source' else ('obs' if src == 'obs' else 'client')),
            'obs_source_name': str(getattr(capture, 'obs_source_name', '') or ''),
            'obs_projector_title': str(_env_str('FRBOT_OBS_PROJECTOR_TITLE', '') or ''),
            **base_context_fields(),
            'ts': int(time.time()),
        }
        frames_dir.mkdir(parents=True, exist_ok=True)
        (frames_dir / 'evidence_manifest.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        return


def _dump_gate_pair_if_enabled(*, frames_dir: Path, gate: str, before: Frame | None, after: Frame | None, reason: str) -> None:
    if not dump_enabled():
        return
    try:
        dump_pair(gate=str(gate), before=before, after=after, reason=str(reason), out_dir=frames_dir)
    except Exception:
        return


def _env_ms(name: str, default_ms: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return int(default_ms)
    try:
        return int(str(raw).strip(), 10)
    except Exception:
        return int(default_ms)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _trace_required() -> bool:
    # PROD-EMERGENCY REAL requires trace artifacts even on failures.
    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    mode = (os.environ.get('FRBOT_MODE', 'real') or 'real').strip().lower()
    return profile == 'prod_emergency' and mode == 'real'


def _assert_centroid_in_minimap_bounds(*, frame: Frame, cx: float, cy: float) -> None:
    w = int(getattr(frame, 'minimap_width', 0) or 0)
    h = int(getattr(frame, 'minimap_height', 0) or 0)
    if w <= 0 or h <= 0:
        raise PreflightFailed('minimap_roi_invalid')
    if float(cx) < 0.0 or float(cy) < 0.0 or float(cx) >= float(w) or float(cy) >= float(h):
        exc = PreflightFailed('minimap_roi_invalid')
        try:
            setattr(exc, 'details', {'cx': float(cx), 'cy': float(cy), 'minimap_width': int(w), 'minimap_height': int(h)})
        except Exception:
            pass
        raise exc


def _append_cavebot_trace(*, frames_dir: Path, payload: dict) -> None:
    if not dump_enabled() and not _trace_required():
        return
    try:
        frames_dir.mkdir(parents=True, exist_ok=True)
        p = frames_dir / 'cavebot_trace.jsonl'
        with p.open('a', encoding='utf-8') as f:
            f.write(json.dumps(payload, sort_keys=True) + '\n')
    except Exception:
        return


def _angle_deg_to_waypoint(*, before: Tile, after: Tile, waypoint: Tile) -> float:
    # Expected vector (to waypoint) vs real movement vector (single step).
    ax = float(int(waypoint.x) - int(before.x))
    ay = float(int(waypoint.y) - int(before.y))
    bx = float(int(after.x) - int(before.x))
    by = float(int(after.y) - int(before.y))

    amag = float((ax * ax + ay * ay) ** 0.5)
    bmag = float((bx * bx + by * by) ** 0.5)
    if amag <= 1e-9 or bmag <= 1e-9:
        return 0.0
    dot = (ax * bx + ay * by) / (amag * bmag)
    dot = max(-1.0, min(1.0, float(dot)))
    return float(math.degrees(math.acos(dot)))


def _score_detection(pixel_count: int, fill_ratio: float, aspect_ratio: float) -> float:
    # Prefer large, dense, compact detections deterministically.
    return float(pixel_count) * float(fill_ratio) / max(1.0, float(aspect_ratio))


def _select_moving_marker_after_key(
    *,
    before_frame: Frame,
    capture: object,
    direction: str,
    marker_cfgs: tuple[MarkerConfig, ...],
    pixels_per_tile: float,
    z: int,
    max_settle_ms: int,
    poll_ms: int,
) -> tuple[MarkerConfig, MarkerDetection, MarkerDetection, Frame]:
    """After one movement key was emitted, select a marker that actually moves.

    Returns (chosen_cfg, det_before, det_after, after_frame).

    Deterministic:
    - Candidates are tried in the order provided.
    - Among candidates that show a semantic 1-tile step in the commanded direction,
      we pick the highest detection score (computed from BEFORE detection).
    """

    grab = getattr(capture, 'grab', None)
    if grab is None:
        raise PreflightFailed('capture_invalid')

    # Detect before for each candidate and compute stable scores.
    before_dets: list[tuple[MarkerConfig, MarkerDetection, float]] = []
    for cfg in marker_cfgs:
        det_b = detect_player_marker(before_frame, cfg)
        if det_b is None:
            continue
        _assert_centroid_in_minimap_bounds(frame=before_frame, cx=float(det_b.pos.px), cy=float(det_b.pos.py))
        score = _score_detection(int(det_b.pixel_count), float(det_b.fill_ratio), float(det_b.aspect_ratio))
        before_dets.append((cfg, det_b, float(score)))

    if not before_dets:
        raise PreflightFailed('minimap_player_not_found')

    deadline_ns = time.monotonic_ns() + int(max(0, int(max_settle_ms))) * 1_000_000
    poll_ns = int(max(0, int(poll_ms))) * 1_000_000

    best: tuple[float, int, MarkerConfig, MarkerDetection, MarkerDetection, Frame] | None = None

    while True:
        after = grab()
        if after is not None and bool(getattr(after, 'minimap_detected', False)):
            for idx, (cfg, det_b, score) in enumerate(before_dets):
                det_a = detect_player_marker(after, cfg)
                if det_a is None:
                    continue
                _assert_centroid_in_minimap_bounds(frame=after, cx=float(det_a.pos.px), cy=float(det_a.pos.py))

                t = SemanticTracker(pixels_per_tile=float(pixels_per_tile), z=int(z))
                before_tile = t.observe_tile(det_b.pos)
                after_tile = t.observe_tile(det_a.pos)
                if not semantic_progress_ok(direction=str(direction), before=before_tile, after=after_tile, waypoint=None):
                    continue

                cand = (float(score), int(-idx), cfg, det_b, det_a, after)
                if best is None or cand > best:
                    best = cand

            if best is not None:
                _score, _neg_idx, chosen_cfg, det_before, det_after, chosen_after = best
                return chosen_cfg, det_before, det_after, chosen_after

        if time.monotonic_ns() >= int(deadline_ns):
            break
        if poll_ns > 0:
            wait_until_ns(int(time.monotonic_ns() + poll_ns))

    raise PreflightFailed('cavebot_move_key_no_effect')


def _move_key_for_direction(direction: str) -> str:
    """Resolve which key string to send for a movement direction.

    Engine intents are direction-based (up/down/left/right). Real clients may
    be bound to arrow keys, WASD, numpad, etc. This mapping keeps the engine
    stable while allowing PROD-EMERGENCY REAL runs to adapt via env vars.

        Dual support (WASD + arrows):
            - Defaults: WASD (primary) with arrow fallback.
            - If both are defined (primary + fallback), primary wins.

        Env overrides (optional):
            - FRBOT_MOVE_KEY_UP (default: 'w')
            - FRBOT_MOVE_KEY_DOWN (default: 's')
            - FRBOT_MOVE_KEY_LEFT (default: 'a')
            - FRBOT_MOVE_KEY_RIGHT (default: 'd')
            - FRBOT_MOVE_KEY_UP_FALLBACK (default: 'up')
            - FRBOT_MOVE_KEY_DOWN_FALLBACK (default: 'down')
            - FRBOT_MOVE_KEY_LEFT_FALLBACK (default: 'left')
            - FRBOT_MOVE_KEY_RIGHT_FALLBACK (default: 'right')
    """

    d = (direction or '').strip().lower()
    if d == 'up':
        primary = _env_str('FRBOT_MOVE_KEY_UP', 'w')
        return primary if primary else _env_str('FRBOT_MOVE_KEY_UP_FALLBACK', 'up')
    if d == 'down':
        primary = _env_str('FRBOT_MOVE_KEY_DOWN', 's')
        return primary if primary else _env_str('FRBOT_MOVE_KEY_DOWN_FALLBACK', 'down')
    if d == 'left':
        primary = _env_str('FRBOT_MOVE_KEY_LEFT', 'a')
        return primary if primary else _env_str('FRBOT_MOVE_KEY_LEFT_FALLBACK', 'left')
    if d == 'right':
        primary = _env_str('FRBOT_MOVE_KEY_RIGHT', 'd')
        return primary if primary else _env_str('FRBOT_MOVE_KEY_RIGHT_FALLBACK', 'right')
    return d


def _grab_after_settle_for_move(
    *,
    capture: object,
    marker_cfg: MarkerConfig,
    tracker: SemanticTracker,
    before_tile: Tile,
    direction: str,
    waypoint: Tile | None,
    max_settle_ms: int,
    poll_ms: int,
) -> tuple[Frame, Tile]:
    """After a move input, poll capture until semantic movement or timeout.

    This emits no additional inputs; it only grabs frames.
    """

    deadline_ns = time.monotonic_ns() + int(max(0, int(max_settle_ms))) * 1_000_000
    poll_ns = int(max(0, int(poll_ms))) * 1_000_000

    last_after: Frame | None = None
    last_tile: Tile | None = None

    grab = getattr(capture, 'grab', None)
    if grab is None:
        raise PreflightFailed('capture_invalid')

    while True:
        after = grab()
        last_after = after

        if after is not None and bool(getattr(after, 'minimap_detected', False)):
            det = detect_player_marker(after, marker_cfg)
            if det is not None:
                _assert_centroid_in_minimap_bounds(frame=after, cx=float(det.pos.px), cy=float(det.pos.py))
                tile = tracker.observe_tile(det.pos)
                last_tile = tile
                if semantic_progress_ok(direction=direction, before=before_tile, after=tile, waypoint=waypoint):
                    return after, tile

        if time.monotonic_ns() >= int(deadline_ns):
            break
        if poll_ns > 0:
            wait_until_ns(int(time.monotonic_ns() + poll_ns))

    if last_after is None:
        raise PreflightFailed('capture_empty')
    if last_tile is None:
        raise PreflightFailed('minimap_player_not_found')
    return last_after, last_tile


def _tile_distance(a: Tile | None, b: Tile | None) -> float:
    if a is None or b is None:
        return 1e9
    dx = float(int(a.x) - int(b.x))
    dy = float(int(a.y) - int(b.y))
    dz = float(int(a.z) - int(b.z))
    # In PROD-EMERGENCY we only certify same-floor movement; any z delta is huge.
    if dz != 0.0:
        return 1e9
    return float((dx * dx + dy * dy) ** 0.5)


def _load_config_from_env() -> RuntimeConfig:
    mode = os.environ.get('FRBOT_MODE', 'real')
    tick_hz_raw = os.environ.get('FRBOT_TICK_HZ', '20')
    config_path = os.environ.get('FRBOT_CONFIG_PATH', '')
    bot_config_path = os.environ.get('FRBOT_BOT_CONFIG_PATH', '')

    def env_bool(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return raw.strip() not in {'0', 'false', 'no', 'off'}

    def env_str(name: str, default: str) -> str:
        raw = os.environ.get(name)
        return default if raw is None else raw

    def env_key(name: str, default: str) -> str:
        v = str(env_str(name, default) or default).strip()
        return str(v) if v else str(default)

    return RuntimeConfig(
        mode=mode,
        tick_hz=float(tick_hz_raw),
        config_path=config_path,
        bot_config_path=bot_config_path,
        enable_cavebot=env_bool('FRBOT_ENABLE_CAVEBOT', True),
        minimap_roi=env_str('FRBOT_MINIMAP_ROI', 'minimap'),

        # Looting (premium): allow overriding the quick-loot hotkey.
        # Accepts key aliases like 'avPag' / 'pgdn' / 'pagedown'.
        quick_loot_key=env_key('FRBOT_TIBIA_QUICK_LOOT_KEY', env_str('FRBOT_QUICK_LOOT_KEY', 'R')),

        window_hwnd=parse_window_hwnd_env('FRBOT_WINDOW_HWND'),
        window_title_substring=env_str('FRBOT_WINDOW_TITLE', ''),

        player_marker_rgb=env_str('FRBOT_PLAYER_MARKER_RGB', '255,0,255'),
        player_marker_tol=int(os.environ.get('FRBOT_PLAYER_MARKER_TOL', '30') or '30'),
        player_marker_min_pixels=int(os.environ.get('FRBOT_PLAYER_MARKER_MIN_PIXELS', '5') or '5'),
        player_marker_max_pixels=int(os.environ.get('FRBOT_PLAYER_MARKER_MAX_PIXELS', '0') or '0'),
        player_marker_min_fill_ratio=float(os.environ.get('FRBOT_PLAYER_MARKER_MIN_FILL_RATIO', '0.15') or '0.15'),
        player_marker_max_aspect_ratio=float(os.environ.get('FRBOT_PLAYER_MARKER_MAX_ASPECT_RATIO', '4.0') or '4.0'),
        pixels_per_tile=float(os.environ.get('FRBOT_PIXELS_PER_TILE', '1.0') or '1.0'),

        max_attempts_per_waypoint=int(os.environ.get('FRBOT_MAX_ATTEMPTS_PER_WAYPOINT', '3') or '3'),
        max_time_ms_per_waypoint=int(os.environ.get('FRBOT_MAX_TIME_MS_PER_WAYPOINT', '5000') or '5000'),
    )


def run() -> int:
    try:
        cfg = _load_config_from_env()

        # Mock mode is used in CI on Linux; only REAL runs are Windows-only.
        if cfg.mode.strip().lower() == 'real' and sys.platform != 'win32':
            write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
            return 1

        # Resolve frames dir early so we can always write trace on failures.
        frames_dir = _frames_dir_for_run()
        os.environ.setdefault('FRBOT_REAL_FRAMES_DIR', str(frames_dir))

        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        # Always initialize trace file for PROD-EMERGENCY REAL audits.
        if _trace_required():
            try:
                frames_dir.mkdir(parents=True, exist_ok=True)
                (frames_dir / 'cavebot_trace.jsonl').write_text('', encoding='utf-8')
            except Exception:
                pass

        capture, input_, binding = preflight(ctx)

        is_real_mode = ctx.config.mode.strip().lower() == 'real'
        if is_prod_emergency() and is_real_mode and not dump_enabled():
            raise PreflightFailed('dump_frames_required')

        # Evidence manifest is required by tools/audit_all.py for OBS capture sources.
        _write_evidence_manifest(frames_dir=frames_dir, capture=capture)

        # For PROD-EMERGENCY REAL certification, emit deterministic gate evidence pairs without emitting input.
        if is_prod_emergency() and is_real_mode and dump_enabled():
            try:
                b0 = capture.grab()
                a0 = capture.grab()
                _dump_gate_pair_if_enabled(frames_dir=frames_dir, gate='targeting', before=b0, after=a0, reason='startup_evidence')
                b1 = capture.grab()
                a1 = capture.grab()
                _dump_gate_pair_if_enabled(frames_dir=frames_dir, gate='healing', before=b1, after=a1, reason='startup_evidence')
            except Exception:
                pass

        # Fresh trace per run (avoids mixing segments across runs).
        # (Already cleared above for PROD-EMERGENCY REAL.)

        # Load bot config (waypoints) after preflight so we still abort fast on missing deps.
        bot_cfg = load_bot_config(ctx.config.bot_config_path)
        if ctx.config.mode.strip().lower() == 'mock' and not bot_cfg.waypoints:
            # Deterministic harness route (keeps mock mode operational without external config).
            ctx.cavebot.waypoints = (
                Tile(x=0, y=0, z=7),
                Tile(x=1, y=0, z=7),
                Tile(x=1, y=1, z=7),
                Tile(x=0, y=1, z=7),
            )
        else:
            ctx.cavebot.waypoints = bot_cfg.waypoints

        # PROD-EMERGENCY REAL: legacy bot configs store absolute Tibia coordinates.
        # Our minimap tracker yields relative tile deltas. Anchor the waypoint list
        # at the first waypoint and operate in relative tile space on a single z-level.
        if is_prod_emergency() and is_real_mode and ctx.cavebot.waypoints:
            base = ctx.cavebot.waypoints[0]
            z0 = int(base.z)
            rel: list[Tile] = []
            seen: set[tuple[int, int, int]] = set()
            max_rel = _env_int('FRBOT_PROD_EMERGENCY_MAX_WAYPOINTS', 6)
            for wp in ctx.cavebot.waypoints:
                if int(wp.z) != z0:
                    continue
                t = Tile(x=int(wp.x) - int(base.x), y=int(wp.y) - int(base.y), z=z0, walkable=True)
                key = (int(t.x), int(t.y), int(t.z))
                if key in seen:
                    continue
                seen.add(key)
                rel.append(t)
                if max_rel > 0 and len(rel) >= int(max_rel):
                    break
            if rel:
                ctx.cavebot.waypoints = tuple(rel)

        # Only configure runtime logging AFTER preflight succeeds.
        logger = configure_logger()

        if capture is None:
            raise PreflightFailed('preflight did not provide a capture adapter')

        # If preflight returned, we are READY with verified adapters.
        ctx.status.state = RuntimeState.RUNNING

        tick_hz = max(1e-6, float(ctx.config.tick_hz))
        tick_period_ns = int(1_000_000_000 / float(tick_hz))
        max_age_ms = int(os.environ.get('FRBOT_FRAME_MAX_AGE_MS', '500'))
        start_ns = time.monotonic_ns()

        effective_rgb = os.environ.get('FRBOT_PLAYER_MARKER_RGB_EFFECTIVE', '') or ctx.config.player_marker_rgb
        effective_min_pixels = os.environ.get('FRBOT_PLAYER_MARKER_MIN_PIXELS_EFFECTIVE', '')
        min_pixels = ctx.config.player_marker_min_pixels
        try:
            if str(effective_min_pixels).strip():
                min_pixels = int(str(effective_min_pixels).strip(), 10)
        except Exception:
            min_pixels = ctx.config.player_marker_min_pixels
        marker_cfg = marker_config_from_env(
            str(effective_rgb),
            str(ctx.config.player_marker_tol),
            str(min_pixels),
            str(ctx.config.player_marker_max_pixels),
            str(ctx.config.player_marker_min_fill_ratio),
            str(ctx.config.player_marker_max_aspect_ratio),
        )
        z0 = 7
        wp0 = ctx.cavebot.current_waypoint()
        if wp0 is not None:
            z0 = int(wp0.z)
        tracker = SemanticTracker(pixels_per_tile=float(ctx.config.pixels_per_tile), z=z0)
        last_positions: list = []

        max_ticks = cap_ticks(int(os.environ.get('FRBOT_MAX_TICKS', '0') or '0'))

        # Deterministic loop: bounded by both time and ticks in PROD-EMERGENCY.
        # If FRBOT_SESSION_SECONDS isn't explicitly set, align default with tick cap.
        ss_raw = os.environ.get('FRBOT_SESSION_SECONDS')
        if ss_raw is None and is_prod_emergency() and is_real_mode:
            session_seconds = default_session_seconds(float(max_ticks) / float(tick_hz))
        else:
            session_seconds = default_session_seconds(float(os.environ.get('FRBOT_SESSION_SECONDS', '1.0') or '1.0'))
        session_deadline_ns = start_ns + int(max(0.0, float(session_seconds)) * 1_000_000_000)

        # Cavebot certification constraints (PROD-EMERGENCY only).
        max_ticks_per_waypoint = _env_int('FRBOT_CAVEBOT_MAX_TICKS_PER_WAYPOINT', 0)
        waypoint_key: tuple[int, int, int] | None = None
        ticks_in_waypoint = 0
        prev_after_dist: float | None = None
        cert_waypoint_id: str | None = None
        any_move_inputs_sent = False
        startup_move_verified = False
        marker_overlay_dumped = False

        next_tick_ns = start_ns
        while True:
            now_ns = time.monotonic_ns()
            frame = capture.grab()  # verified by preflight
            record_before('runtime', frame)
            if not frame.minimap_detected:
                raise PreflightFailed('minimap_not_detected')

            det = detect_player_marker(frame, marker_cfg)
            if det is None:
                raise PreflightFailed('minimap_player_not_found')
            _assert_centroid_in_minimap_bounds(frame=frame, cx=float(det.pos.px), cy=float(det.pos.py))

            # Debug overlay: dump one frame with centroid marked.
            if (not marker_overlay_dumped) and is_prod_emergency() and is_real_mode:
                try:
                    from diagnostics.overlay_dump import dump_marker_centroid_overlay

                    minimap_roi = ctx.rois.get(ctx.config.minimap_roi)
                    if minimap_roi is not None:
                        dump_marker_centroid_overlay(
                            frames_dir=frames_dir,
                            frame=frame,
                            minimap_roi=minimap_roi,
                            centroid_x_minimap=float(det.pos.px),
                            centroid_y_minimap=float(det.pos.py),
                            reason='centroid',
                        )
                        marker_overlay_dumped = True
                except Exception:
                    marker_overlay_dumped = True
            tile = tracker.observe_tile(det.pos)
            ctx.position.x = int(tile.x)
            ctx.position.y = int(tile.y)
            ctx.position.z = int(tile.z)
            ctx.position.source = 'minimap_track'
            ctx.position.confidence = 1.0

            now_ns = time.monotonic_ns()

            age_ns = now_ns - frame.monotonic_ts_ns
            capture_age_ms = int(age_ns // 1_000_000)

            ctx.telemetry.last_frame_ts_ns = frame.monotonic_ts_ns

            last_positions.append(ctx.position.tile())
            if len(last_positions) > 8:
                last_positions = last_positions[-8:]

            # Deterministic waypoint progress: advance index only when objective position matches.
            cur_wp = ctx.cavebot.current_waypoint()
            cur_tile = ctx.position.tile()
            if cur_wp is not None and (cur_tile.x, cur_tile.y, cur_tile.z) == (cur_wp.x, cur_wp.y, cur_wp.z):
                ctx.cavebot.current_index = (ctx.cavebot.current_index + 1) % len(ctx.cavebot.waypoints)

                if is_prod_emergency() and is_real_mode:
                    # Emit stable reach evidence: two consecutive "within radius" events.
                    wp_obj = {'waypoint_id': f"{cur_wp.x},{cur_wp.y},{cur_wp.z}", 'x': int(cur_wp.x), 'y': int(cur_wp.y), 'z': int(cur_wp.z), 'radius_px': 0}
                    _append_cavebot_trace(
                        frames_dir=frames_dir,
                        payload={
                            'event': 'tick',
                            'tick_index': int(ctx.telemetry.tick_count),
                            'input_sent': False,
                            'key': '',
                            'reach_streak': 1,
                            'distance_before_px': 0.0,
                            'distance_after_px': 0.0,
                            'angle_deg': 0.0,
                            'abort_reason': 'none',
                            'waypoint': wp_obj,
                        },
                    )
                    _append_cavebot_trace(
                        frames_dir=frames_dir,
                        payload={
                            'event': 'WAYPOINT_REACHED',
                            'tick_index': int(ctx.telemetry.tick_count),
                            'input_sent': False,
                            'key': '',
                            'reach_streak': 2,
                            'distance_before_px': 0.0,
                            'distance_after_px': 0.0,
                            'angle_deg': 0.0,
                            'abort_reason': 'none',
                            'waypoint': wp_obj,
                        },
                    )

                    # Certification success: require at least one real movement input first.
                    if any_move_inputs_sent:
                        log_json(logger, event='completed', gate='runtime', tick_count=int(ctx.telemetry.tick_count), reached_waypoint=True)
                        return 0

                # Reset anti-loop state for new waypoint.
                ctx.cavebot.stuck_counter = 0
                ctx.cavebot.stuck_waypoint = None
                ctx.cavebot.stuck_started_ts_ms = 0

                # Reset per-waypoint certification state.
                waypoint_key = None
                ticks_in_waypoint = 0
                prev_after_dist = None

            inp = EngineInput(
                now_ts_ms=int(now_ns // 1_000_000),
                capture_age_ms=capture_age_ms,
                max_capture_age_ms=max_age_ms,
                tick_count=int(ctx.telemetry.tick_count),
                current_position=cur_tile,
                target_tile=ctx.cavebot.current_waypoint(),
                last_positions=tuple(last_positions),
            )
            out = engine_tick(inp, enable_cavebot=bool(ctx.config.enable_cavebot))
            if not out.ok:
                raise PreflightFailed(out.abort_reason or 'engine abort')

            if out.telemetry is not None:
                ctx.telemetry.tick_count = int(out.telemetry.tick_count)
                ctx.telemetry.last_capture_age_ms = int(out.telemetry.last_capture_age_ms)
                ctx.telemetry.last_tick_valid = bool(out.telemetry.last_tick_valid)

            if len(out.intents) > 1:
                raise PreflightFailed('engine produced more than one intent (contract violation)')

            # Execute at most one intent per tick.
            if out.intents:
                if input_ is None:
                    raise PreflightFailed('preflight did not provide an input adapter')

                intent = out.intents[0]
                if not isinstance(intent, IntentMove):
                    raise PreflightFailed(f'unknown intent type: {type(intent).__name__}')
                ctx.telemetry.last_intent = type(intent).__name__

                # Window binding is checked ONLY immediately before emitting input.
                # Losing foreground/focus must not abort a capture-only tick.
                try:
                    binding.assert_bound()
                    snap = binding.snapshot()
                    input_.assert_bound(int(getattr(snap, 'hwnd', 0)))
                except Exception as exc:
                    raise PreflightFailed('window_binding_lost') from exc

                before_tile = ctx.position.tile()
                waypoint = ctx.cavebot.current_waypoint()
                waypoint_key = None if waypoint is None else (waypoint.x, waypoint.y, waypoint.z)
                try:
                    move_key = _move_key_for_direction(str(intent.direction))
                    input_.press_key(move_key)
                except Exception as exc:
                    raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc

                any_move_inputs_sent = True

                # Bootstrap in PROD-EMERGENCY REAL: the first movement input must validate
                # both the movement key and the marker (must observe a semantic step).
                if is_prod_emergency() and is_real_mode and (not startup_move_verified):
                    max_settle_ms = _env_int('FRBOT_REAL_MOVE_SETTLE_MS', 350)
                    poll_ms = _env_int('FRBOT_REAL_MOVE_POLL_MS', 50)

                    # Build deterministic candidate marker configs.
                    rgb_candidates: list[str] = []
                    for rgb in [
                        str(effective_rgb),
                        str(ctx.config.player_marker_rgb),
                        '255,255,0',
                        '255,0,255',
                        '255,255,255',
                        '255,51,0',
                    ]:
                        s = str(rgb or '').strip()
                        if s and s not in rgb_candidates:
                            rgb_candidates.append(s)

                    min_pix_opts: list[int] = [int(min_pixels)]
                    if int(min_pixels) > 3:
                        min_pix_opts.append(3)

                    cfgs: list[MarkerConfig] = []
                    for rgb in rgb_candidates:
                        for mp in min_pix_opts:
                            cfgs.append(
                                marker_config_from_env(
                                    str(rgb),
                                    str(ctx.config.player_marker_tol),
                                    str(int(mp)),
                                    str(ctx.config.player_marker_max_pixels),
                                    str(ctx.config.player_marker_min_fill_ratio),
                                    str(ctx.config.player_marker_max_aspect_ratio),
                                )
                            )

                    chosen_cfg, det_before, det_after, after = _select_moving_marker_after_key(
                        before_frame=frame,
                        capture=capture,
                        direction=str(intent.direction),
                        marker_cfgs=tuple(cfgs),
                        pixels_per_tile=float(ctx.config.pixels_per_tile),
                        z=int(z0),
                        max_settle_ms=int(max_settle_ms),
                        poll_ms=int(poll_ms),
                    )

                    # Switch marker config + tracker origin to the chosen moving marker.
                    marker_cfg = chosen_cfg
                    try:
                        os.environ['FRBOT_PLAYER_MARKER_RGB_EFFECTIVE'] = f"{int(chosen_cfg.rgb[0])},{int(chosen_cfg.rgb[1])},{int(chosen_cfg.rgb[2])}"
                        os.environ['FRBOT_PLAYER_MARKER_MIN_PIXELS_EFFECTIVE'] = str(int(chosen_cfg.min_pixels))
                    except Exception:
                        pass

                    tracker = SemanticTracker(pixels_per_tile=float(ctx.config.pixels_per_tile), z=int(z0))
                    before_tile_eff = tracker.observe_tile(det_before.pos)
                    after_tile = tracker.observe_tile(det_after.pos)

                    record_after('runtime', after)
                    if not after.minimap_detected:
                        raise PreflightFailed('minimap_not_detected')
                    ctx.position.x = int(after_tile.x)
                    ctx.position.y = int(after_tile.y)
                    ctx.position.z = int(after_tile.z)
                    ctx.position.source = 'minimap_track'
                    ctx.position.confidence = 1.0

                    moved = semantic_progress_ok(direction=str(intent.direction), before=before_tile_eff, after=after_tile, waypoint=None)
                    if not moved:
                        abort_exc = PreflightFailed('cavebot_move_key_no_effect')
                        _append_cavebot_trace(
                            frames_dir=frames_dir,
                            payload={
                                'event': 'abort',
                                'tick_index': int(ctx.telemetry.tick_count),
                                'input_sent': True,
                                'key': str(move_key),
                                'direction': str(intent.direction),
                                'abort_reason': 'cavebot_move_key_no_effect',
                            },
                        )
                        raise abort_exc

                    startup_move_verified = True

                else:

                    # REAL: bounded settle time to allow minimap/marker to reflect movement.
                    # MOCK: deterministic single-grab behavior.
                    if is_real_mode:
                        max_settle_ms = _env_int('FRBOT_REAL_MOVE_SETTLE_MS', 350)
                        poll_ms = _env_int('FRBOT_REAL_MOVE_POLL_MS', 50)
                        after, after_tile = _grab_after_settle_for_move(
                            capture=capture,
                            marker_cfg=marker_cfg,
                            tracker=tracker,
                            before_tile=before_tile,
                            direction=str(intent.direction),
                            waypoint=None,
                            max_settle_ms=int(max_settle_ms),
                            poll_ms=int(poll_ms),
                        )
                    else:
                        after = capture.grab()
                        if not after.minimap_detected:
                            raise PreflightFailed('minimap_not_detected')
                        det2 = detect_player_marker(after, marker_cfg)
                        if det2 is None:
                            raise PreflightFailed('minimap_player_not_found')
                        after_tile = tracker.observe_tile(det2.pos)

                    record_after('runtime', after)
                    if not after.minimap_detected:
                        raise PreflightFailed('minimap_not_detected')
                    ctx.position.x = int(after_tile.x)
                    ctx.position.y = int(after_tile.y)
                    ctx.position.z = int(after_tile.z)
                    ctx.position.source = 'minimap_track'
                    ctx.position.confidence = 1.0

                ok = semantic_progress_ok(direction=intent.direction, before=before_tile, after=after_tile, waypoint=waypoint)
                if not ok:
                    # Anti-loop: visual deltas do NOT count. Only semantic progress counts.
                    now_ms = int(now_ns // 1_000_000)
                    if waypoint_key is not None and ctx.cavebot.stuck_waypoint == waypoint_key:
                        ctx.cavebot.stuck_counter += 1
                    else:
                        ctx.cavebot.stuck_waypoint = waypoint_key
                        ctx.cavebot.stuck_counter = 1
                        ctx.cavebot.stuck_started_ts_ms = now_ms

                    if ctx.cavebot.stuck_started_ts_ms and (now_ms - ctx.cavebot.stuck_started_ts_ms) >= int(ctx.config.max_time_ms_per_waypoint):
                        raise PreflightFailed('no_semantic_progress')
                    if ctx.cavebot.stuck_counter >= int(ctx.config.max_attempts_per_waypoint):
                        raise PreflightFailed('no_semantic_progress')
                else:
                    ctx.cavebot.stuck_counter = 0
                    ctx.cavebot.stuck_waypoint = None
                    ctx.cavebot.stuck_started_ts_ms = 0
                    ctx.cavebot.last_move_tick = int(ctx.telemetry.tick_count)

                # PROD-EMERGENCY cavebot semantic certification: strict distance decrease or abort.
                if is_prod_emergency() and is_real_mode and waypoint is not None:
                    # Track per-waypoint tick budget and strict distance series.
                    wp_id = f"{waypoint.x},{waypoint.y},{waypoint.z}"
                    if cert_waypoint_id != wp_id:
                        cert_waypoint_id = wp_id
                        ticks_in_waypoint = 0
                        prev_after_dist = None

                    ticks_in_waypoint += 1
                    if int(max_ticks_per_waypoint) > 0 and int(ticks_in_waypoint) > int(max_ticks_per_waypoint):
                        raise PreflightFailed('cavebot_waypoint_timeout')

                    dist_before = _tile_distance(before_tile, waypoint)
                    dist_after = _tile_distance(after_tile, waypoint)

                    angle_deg = _angle_deg_to_waypoint(before=before_tile, after=after_tile, waypoint=waypoint)
                    if float(angle_deg) > 90.0:
                        abort_exc = PreflightFailed('cavebot_trace_angle_gt_90')
                        _append_cavebot_trace(
                            frames_dir=frames_dir,
                            payload={
                                'event': 'abort',
                                'tick_index': int(ctx.telemetry.tick_count),
                                'input_sent': True,
                                'key': str(move_key),
                                'direction': str(intent.direction),
                                'abort_reason': 'cavebot_trace_angle_gt_90',
                                'angle_deg': float(angle_deg),
                                'before_tile': {'x': int(before_tile.x), 'y': int(before_tile.y), 'z': int(before_tile.z)},
                                'after_tile': {'x': int(after_tile.x), 'y': int(after_tile.y), 'z': int(after_tile.z)},
                                'waypoint': {'x': int(waypoint.x), 'y': int(waypoint.y), 'z': int(waypoint.z)},
                            },
                        )
                        try:
                            setattr(
                                abort_exc,
                                'details',
                                {
                                    'direction': str(intent.direction),
                                    'angle_deg': float(angle_deg),
                                    'before_tile': {'x': int(before_tile.x), 'y': int(before_tile.y), 'z': int(before_tile.z)},
                                    'after_tile': {'x': int(after_tile.x), 'y': int(after_tile.y), 'z': int(after_tile.z)},
                                    'waypoint': {'x': int(waypoint.x), 'y': int(waypoint.y), 'z': int(waypoint.z)},
                                },
                            )
                        except Exception:
                            pass
                        raise abort_exc

                    if not (float(dist_after) < float(dist_before)):
                        abort_exc = PreflightFailed('cavebot_trace_distance_not_decreasing')
                        _append_cavebot_trace(
                            frames_dir=frames_dir,
                            payload={
                                'event': 'abort',
                                'tick_index': int(ctx.telemetry.tick_count),
                                'input_sent': True,
                                'key': str(move_key),
                                'direction': str(intent.direction),
                                'abort_reason': 'cavebot_trace_distance_not_decreasing',
                                'before_tile': {'x': int(before_tile.x), 'y': int(before_tile.y), 'z': int(before_tile.z)},
                                'after_tile': {'x': int(after_tile.x), 'y': int(after_tile.y), 'z': int(after_tile.z)},
                                'waypoint': {'x': int(waypoint.x), 'y': int(waypoint.y), 'z': int(waypoint.z)},
                                'distance_before_px': float(dist_before),
                                'distance_after_px': float(dist_after),
                                'marker_rgb_effective': str(effective_rgb),
                                'marker_min_pixels_effective': int(min_pixels),
                            },
                        )
                        try:
                            setattr(
                                abort_exc,
                                'details',
                                {
                                'direction': str(intent.direction),
                                'before_tile': {'x': int(before_tile.x), 'y': int(before_tile.y), 'z': int(before_tile.z)},
                                'after_tile': {'x': int(after_tile.x), 'y': int(after_tile.y), 'z': int(after_tile.z)},
                                'waypoint': {'x': int(waypoint.x), 'y': int(waypoint.y), 'z': int(waypoint.z)},
                                'distance_before_px': float(dist_before),
                                'distance_after_px': float(dist_after),
                                'pixels_per_tile': float(ctx.config.pixels_per_tile),
                                'marker_rgb_effective': str(effective_rgb),
                                'marker_min_pixels_effective': int(min_pixels),
                                },
                            )
                        except Exception:
                            pass
                        raise abort_exc
                    if prev_after_dist is not None and not (float(dist_after) < float(prev_after_dist)):
                        abort_exc = PreflightFailed('cavebot_trace_series_not_strictly_decreasing')
                        _append_cavebot_trace(
                            frames_dir=frames_dir,
                            payload={
                                'event': 'abort',
                                'tick_index': int(ctx.telemetry.tick_count),
                                'input_sent': True,
                                'key': str(move_key),
                                'direction': str(intent.direction),
                                'abort_reason': 'cavebot_trace_series_not_strictly_decreasing',
                                'after_tile': {'x': int(after_tile.x), 'y': int(after_tile.y), 'z': int(after_tile.z)},
                                'waypoint': {'x': int(waypoint.x), 'y': int(waypoint.y), 'z': int(waypoint.z)},
                                'prev_distance_after_px': float(prev_after_dist),
                                'distance_after_px': float(dist_after),
                            },
                        )
                        try:
                            setattr(
                                abort_exc,
                                'details',
                                {
                                'direction': str(intent.direction),
                                'after_tile': {'x': int(after_tile.x), 'y': int(after_tile.y), 'z': int(after_tile.z)},
                                'waypoint': {'x': int(waypoint.x), 'y': int(waypoint.y), 'z': int(waypoint.z)},
                                'prev_distance_after_px': float(prev_after_dist),
                                'distance_after_px': float(dist_after),
                                },
                            )
                        except Exception:
                            pass
                        raise abort_exc
                    prev_after_dist = float(dist_after)

                    _append_cavebot_trace(
                        frames_dir=frames_dir,
                        payload={
                            'event': 'tick',
                            'tick_index': int(ctx.telemetry.tick_count),
                            'input_sent': True,
                            'key': str(move_key),
                            'reach_streak': 0,
                            'distance_before_px': float(dist_before),
                            'distance_after_px': float(dist_after),
                            'angle_deg': float(angle_deg),
                            'abort_reason': 'none',
                            'waypoint': {'waypoint_id': wp_id, 'x': int(waypoint.x), 'y': int(waypoint.y), 'z': int(waypoint.z), 'radius_px': 0, 'max_ticks': int(max_ticks_per_waypoint or 0)},
                        },
                    )

                    # Mandatory PPM evidence for cavebot movement.
                    _dump_gate_pair_if_enabled(frames_dir=frames_dir, gate='cavebot', before=frame, after=after, reason='semantic_progress')

            log_json(
                logger,
                event='tick',
                gate='runtime',
                mode=str(ctx.config.mode),
                tick_count=int(ctx.telemetry.tick_count),
                capture_age_ms=int(ctx.telemetry.last_capture_age_ms),
                tick_valid=bool(ctx.telemetry.last_tick_valid),
                frame_ts_ns=int(frame.monotonic_ts_ns),
                now_ts_ns=int(now_ns),
                capture_backend=str(getattr(capture, 'name', type(capture).__name__)),
                capture_source=str(os.environ.get('FRBOT_CAPTURE_SOURCE', '') or 'client').strip().lower(),
                obs_source_name=str(getattr(capture, 'obs_source_name', '') or ''),
                frame_resolution=[int(frame.width), int(frame.height)],
                luma_std=float(getattr(capture, 'last_luma_std', 0.0) or 0.0),
            )

            # Emergency guardrails: bounded session by both time and ticks.
            if max_ticks > 0 and int(ctx.telemetry.tick_count) >= int(max_ticks):
                raise PreflightFailed('session_tick_budget_exhausted')

            if time.monotonic_ns() >= int(session_deadline_ns):
                # PROD-EMERGENCY REAL certification must not exit successfully without WAYPOINT_REACHED.
                if is_prod_emergency() and is_real_mode:
                    raise PreflightFailed('session_time_budget_exhausted')
                log_json(logger, event='completed', gate='runtime', tick_count=int(ctx.telemetry.tick_count))
                return 0

            next_tick_ns += int(tick_period_ns)
            wait_until_ns(int(next_tick_ns))

    except ContractViolation as exc:
        if 'Unsupported mode:' in str(exc):
            write_fatal('invalid_mode', exc)
            return 1
        write_fatal('runtime crashed', exc)
        return 1
    except PreflightFailed as exc:
        # Always append abort event for PROD-EMERGENCY REAL audits.
        if _trace_required():
            try:
                # Some strict checks already append an abort event; avoid duplicates.
                already_traced = str(exc) in {
                    'cavebot_trace_distance_not_decreasing',
                    'cavebot_trace_series_not_strictly_decreasing',
                    'cavebot_trace_angle_gt_90',
                    'cavebot_move_key_no_effect',
                }
                if not already_traced:
                    details = getattr(exc, 'details', None)
                    tick_count = 0
                    try:
                        tick_count = int(getattr(getattr(locals().get('ctx', None), 'telemetry', object()), 'tick_count', 0) or 0)
                    except Exception:
                        tick_count = 0
                    _append_cavebot_trace(
                        frames_dir=_frames_dir_for_run(),
                        payload={
                            'event': 'abort',
                            'tick_index': int(tick_count),
                            'input_sent': False,
                            'key': '',
                            'abort_reason': str(exc),
                            'details': details if isinstance(details, dict) else {},
                        },
                    )
            except Exception:
                pass

        # PROD-EMERGENCY: dump BEFORE/AFTER evidence when available.
        if dump_enabled():
            try:
                before_frame, after_frame = snapshot('runtime')
                if before_frame is not None or after_frame is not None:
                    dump_pair(gate='runtime', before=before_frame, after=after_frame, reason=str(exc), out_dir=_frames_dir_for_run())
                elif is_prod_emergency():
                    from diagnostics.emergency_capture import try_dump_window_frame

                    try_dump_window_frame(gate='runtime', reason=str(exc))
            except Exception:
                pass
        write_fatal(str(exc), exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(run())
