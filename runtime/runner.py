from __future__ import annotations

#  BASELINE STABLE 
# Do not modify this loop/invariants without a contract review.
# New features must be added as independent gates (new preflight/runner/entrypoint).
# See BASELINE.md.

import os
import sys
import time

from contracts.errors import ContractViolation, PreflightFailed
from contracts.engine import EngineInput, IntentMove
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.logger import configure_logger
from diagnostics.jsonlog import log as log_json
from core.engine import tick as engine_tick
from runtime.bot_config_loader import load_bot_config
from runtime.env import parse_window_hwnd_env
from runtime.preflight import preflight
from runtime.minimap_semantics import SemanticTracker, detect_player_marker, marker_config_from_env, semantic_progress_ok
from runtime.profile import cap_ticks, default_session_seconds, is_prod_emergency
from runtime.pacing import wait_until_ns


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

    return RuntimeConfig(
        mode=mode,
        tick_hz=float(tick_hz_raw),
        config_path=config_path,
        bot_config_path=bot_config_path,
        enable_cavebot=env_bool('FRBOT_ENABLE_CAVEBOT', True),
        minimap_roi=env_str('FRBOT_MINIMAP_ROI', 'minimap'),

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
        if sys.platform != 'win32':
            write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
            return 1

        cfg = _load_config_from_env()
        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        capture, input_, binding = preflight(ctx)

        # Load bot config (waypoints) after preflight so we still abort fast on missing deps.
        bot_cfg = load_bot_config(ctx.config.bot_config_path)
        if ctx.config.mode.strip().lower() == 'mock' and not bot_cfg.waypoints:
            # Deterministic harness route (keeps mock mode operational without external config).
            from contracts.runtime import Tile

            ctx.cavebot.waypoints = (
                Tile(x=0, y=0, z=7),
                Tile(x=1, y=0, z=7),
                Tile(x=1, y=1, z=7),
                Tile(x=0, y=1, z=7),
            )
        else:
            ctx.cavebot.waypoints = bot_cfg.waypoints

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

        marker_cfg = marker_config_from_env(
            ctx.config.player_marker_rgb,
            str(ctx.config.player_marker_tol),
            str(ctx.config.player_marker_min_pixels),
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

        # Deterministic loop: runs 1s, and logs progress persistently.
        session_seconds = default_session_seconds(float(os.environ.get('FRBOT_SESSION_SECONDS', '1.0') or '1.0'))
        session_deadline_ns = start_ns + int(max(0.0, float(session_seconds)) * 1_000_000_000)
        max_ticks = cap_ticks(int(os.environ.get('FRBOT_MAX_TICKS', '0') or '0'))

        next_tick_ns = start_ns
        while True:
            # Even if we are not about to emit input, we do not proceed without a bound window.
            try:
                binding.assert_bound()
            except Exception:
                raise PreflightFailed('window_binding_lost')

            frame = capture.grab()  # verified by preflight
            if not frame.minimap_detected:
                raise PreflightFailed('minimap_not_detected')

            det = detect_player_marker(frame, marker_cfg)
            if det is None:
                raise PreflightFailed('minimap_player_not_found')
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

                # Reset anti-loop state for new waypoint.
                ctx.cavebot.stuck_counter = 0
                ctx.cavebot.stuck_waypoint = None
                ctx.cavebot.stuck_started_ts_ms = 0

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

                # Hard gate: do not emit input unless window binding is intact.
                try:
                    binding.assert_bound()
                except Exception:
                    raise PreflightFailed('window_binding_lost')

                before_tile = ctx.position.tile()
                waypoint = ctx.cavebot.current_waypoint()
                waypoint_key = None if waypoint is None else (waypoint.x, waypoint.y, waypoint.z)
                try:
                    input_.press_key(intent.direction)
                except Exception as exc:
                    raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc

                after = capture.grab()
                if not after.minimap_detected:
                    raise PreflightFailed('minimap_not_detected')

                det2 = detect_player_marker(after, marker_cfg)
                if det2 is None:
                    raise PreflightFailed('minimap_player_not_found')
                after_tile = tracker.observe_tile(det2.pos)
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
            )

            # Emergency guardrails: bounded session by both time and ticks.
            if max_ticks > 0 and int(ctx.telemetry.tick_count) >= int(max_ticks):
                raise PreflightFailed('session_tick_budget_exhausted')

            if time.monotonic_ns() >= int(session_deadline_ns):
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
        # PROD-EMERGENCY: always attempt to dump a single frame for auditability.
        # This must never create runtime.log; it uses preflight-only capture.
        if is_prod_emergency():
            try:
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
