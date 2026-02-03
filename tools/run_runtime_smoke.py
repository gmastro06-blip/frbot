from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

# Allow running as a script without installing the package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from contracts.errors import ContractViolation, PreflightFailed
from contracts.engine import EngineInput, IntentMove
from contracts.runtime import RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.jsonlog import log as log_json
from diagnostics.logger import configure_logger
from core.engine import tick as engine_tick
from runtime.bot_config_loader import load_bot_config
from runtime.minimap_semantics import SemanticTracker, detect_player_marker, marker_config_from_env, semantic_progress_ok
from runtime.preflight import preflight
from runtime.pacing import sleep_s
from runtime.runner import _load_config_from_env


def _resolve_relative_config_path() -> None:
    cfg_env = (os.environ.get('FRBOT_CONFIG_PATH', '') or '').strip()
    if not cfg_env:
        return
    p = Path(cfg_env)
    if p.is_absolute():
        return
    candidate = (REPO_ROOT / p).resolve()
    if candidate.exists():
        os.environ['FRBOT_CONFIG_PATH'] = str(candidate)


def _rotate_fatal_log(diagnostics_dir: Path) -> None:
    """Rotate fatal.log so it only represents the current run if it reappears.

    This is CI-friendly: after a successful run, fatal.log should not exist.
    """

    try:
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    src = diagnostics_dir / 'fatal.log'
    if not src.exists():
        return

    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    dst = diagnostics_dir / f'fatal.prev.{ts}.log'
    try:
        os.replace(str(src), str(dst))
    except Exception:
        # Best-effort: if rotation fails, try to remove the stale fatal.log.
        try:
            src.unlink(missing_ok=True)
        except Exception:
            pass


def _rotate_runtime_log(diagnostics_dir: Path) -> None:
    """Rotate runtime.log so a single run's events are easy to interpret."""

    try:
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    src = diagnostics_dir / 'runtime.log'
    if not src.exists():
        return

    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    dst = diagnostics_dir / f'runtime.prev.{ts}.log'
    try:
        os.replace(str(src), str(dst))
    except Exception:
        try:
            src.unlink(missing_ok=True)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Deterministic runtime smoke (bounded wall-clock duration).')
    ap.add_argument('--seconds', type=float, default=10.0, help='Wall-clock duration to run (seconds).')
    args = ap.parse_args(argv)

    seconds = float(args.seconds)
    if not (seconds > 0.0):
        print('invalid --seconds (must be > 0)')
        return 2
    # Guard against accidental very long runs in CI.
    seconds = min(seconds, 600.0)

    # Ensure diagnostics dir exists for fatal evidence.
    diagnostics_dir = REPO_ROOT / 'diagnostics'
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    _rotate_fatal_log(diagnostics_dir)
    _rotate_runtime_log(diagnostics_dir)

    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    if profile == 'prod_emergency':
        write_fatal('feature_disabled', details={'tool': 'run_runtime_smoke', 'profile': profile})
        print(json.dumps({'ok': False, 'reason': 'feature_disabled', 'details': {'tool': 'run_runtime_smoke', 'profile': profile}}, ensure_ascii=False))
        return 2

    # Deterministic behavior: this tool always runs the REAL pipeline.
    os.environ['FRBOT_MODE'] = 'real'

    # Make relative config paths deterministic regardless of cwd.
    _resolve_relative_config_path()

    start_ns = time.monotonic_ns()
    deadline_ns = start_ns + int(seconds * 1_000_000_000)

    try:
        cfg = _load_config_from_env()
        cfg = replace(cfg, mode='real')
        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        # Preflight (includes projector focus best-effort, if enabled).
        capture, input_, binding = preflight(ctx)

        # Load bot config (waypoints) after preflight so we still abort fast on missing deps.
        bot_cfg = load_bot_config(ctx.config.bot_config_path)
        if ctx.config.mode.strip().lower() == 'mock' and not bot_cfg.waypoints:
            # Should never happen here (we force real), but keep contract parity.
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
        ctx.status.state = RuntimeState.RUNNING

        tick_period = 1.0 / float(ctx.config.tick_hz)
        max_age_ms = int(os.environ.get('FRBOT_FRAME_MAX_AGE_MS', '500') or '500')

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

        while True:
            now_ns = time.monotonic_ns()
            if now_ns >= deadline_ns:
                total_s = float(now_ns - start_ns) / 1_000_000_000.0
                log_json(
                    logger,
                    event='completed',
                    gate='runtime_smoke',
                    mode=str(ctx.config.mode),
                    tick_count=int(ctx.telemetry.tick_count),
                    runtime_s=total_s,
                )
                return 0

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
                gate='runtime_smoke',
                mode=str(ctx.config.mode),
                tick_count=int(ctx.telemetry.tick_count),
                capture_age_ms=int(ctx.telemetry.last_capture_age_ms),
                tick_valid=bool(ctx.telemetry.last_tick_valid),
                frame_ts_ns=int(frame.monotonic_ts_ns),
                now_ts_ns=int(now_ns),
            )

            sleep_s(float(tick_period))

    except ContractViolation as exc:
        if 'Unsupported mode:' in str(exc):
            write_fatal('invalid_mode', exc)
            return 1
        write_fatal('runtime_smoke_crashed', exc)
        return 1
    except PreflightFailed as exc:
        write_fatal(str(exc), exc)
        return 1
    except Exception as exc:
        write_fatal('runtime_smoke_crashed', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
