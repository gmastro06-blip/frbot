from __future__ import annotations

import os

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.logger import configure_logger
from runtime.cavebot_preflight import run as cavebot_preflight_run
from runtime.cavebot_runner import CavebotTickEvidence, execute_cavebot_tick


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None else raw


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw is not None else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw is not None else float(default)
    except Exception:
        return float(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip() not in {'0', 'false', 'no', 'off'}


def _load_cavebot_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_CAVEBOT_BACKEND', 'real') or 'real').strip().lower()

    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),

        enable_cavebot=True,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,

        minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap'),

        player_marker_rgb=_env_str('FRBOT_PLAYER_MARKER_RGB', '255,0,255'),
        player_marker_tol=_env_int('FRBOT_PLAYER_MARKER_TOL', 30),
        player_marker_min_pixels=_env_int('FRBOT_PLAYER_MARKER_MIN_PIXELS', 5),
        player_marker_max_pixels=_env_int('FRBOT_PLAYER_MARKER_MAX_PIXELS', 0),

        cavebot_max_attempts_per_waypoint=_env_int('FRBOT_CAVEBOT_MAX_ATTEMPTS_PER_WAYPOINT', 3),
        cavebot_max_ticks_per_waypoint=_env_int('FRBOT_CAVEBOT_MAX_TICKS_PER_WAYPOINT', 20),
        cavebot_min_pixel_delta=_env_int('FRBOT_CAVEBOT_MIN_PIXEL_DELTA', 2),

        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
    )


def _fmt_marker(m: object | None) -> str:
    if m is None:
        return 'none'
    try:
        return f"({int(getattr(m, 'x_px'))},{int(getattr(m, 'y_px'))},{int(getattr(m, 'pixel_count'))})"
    except Exception:
        return 'invalid'


def _fmt_progress(ev: CavebotTickEvidence) -> str:
    if ev.progress is None:
        return 'none'
    p = ev.progress
    return f"dx={int(p.delta_x_px)} dy={int(p.delta_y_px)} mag={int(p.delta_mag_px)} db={p.distance_before_px:.2f} da={p.distance_after_px:.2f}"


def run_cavebot_only() -> int:
    """Gate Cavebot mode (minimap + marker tracking).

    Invariants:
    - strong window binding before every input
    - minimap marker must be detected
    - 1 intent -> 1 input -> 1 evidence check
    - semantic progress-or-abort (no hashes, no vague deltas)
    - finite attempts/ticks per waypoint

    Notes:
    - No sleeps; determinism is enforced by guardrails.
    """

    max_total_ticks = _env_int('FRBOT_CAVEBOT_MAX_TICKS', 200)

    ctx: RuntimeContext | None = None

    try:
        cfg = _load_cavebot_config_from_env()
        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        # Preflight must run before we create runtime.log.
        capture, input_, binding = cavebot_preflight_run(ctx)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        for tick_index in range(int(max_total_ticks)):
            wp = ctx.cavebot.current_gate_waypoint()
            if wp is None:
                # Nothing left to do.
                logger.info('SUCCESS cavebot_route_complete inputs_sent=%d', int(ctx.cavebot.gate_inputs_sent))
                return 0

            outcome = execute_cavebot_tick(
                ctx,
                capture=capture,
                input_=input_,
                binding=binding,
                waypoint=wp,
                tick_index=int(tick_index),
            )

            evidence = outcome.evidence

            progress_mag = 'none'
            if evidence.progress is not None:
                progress_mag = str(int(evidence.progress.delta_mag_px))

            logger.info(
                'tick_index=%d waypoint_id=%s marker_before=%s marker_after=%s progress_px=%s attempts_used=%d inputs_sent=%d abort_reason=%s',
                int(tick_index),
                str(wp.waypoint_id),
                _fmt_marker(evidence.marker_before),
                _fmt_marker(evidence.marker_after),
                progress_mag,
                int(ctx.cavebot.gate_attempts_used),
                int(ctx.cavebot.gate_inputs_sent),
                str(outcome.abort_reason or 'none'),
            )

            ctx.telemetry.tick_count += 1

            if outcome.abort_reason is not None:
                raise PreflightFailed(str(outcome.abort_reason))

            if outcome.reached_waypoint:
                ctx.cavebot.gate_waypoint_index += 1
                ctx.cavebot.gate_attempts_used = 0
                ctx.cavebot.gate_ticks_in_waypoint = 0
                # Next tick continues to next waypoint.

        raise PreflightFailed('cavebot_waypoint_stuck')

    except PreflightFailed as exc:
        # Include a snapshot of relevant state directly in the fatal message.
        wp = None
        if ctx is not None:
            try:
                wp = ctx.cavebot.current_gate_waypoint()
            except Exception:
                wp = None

        msg = (
            f"abort_reason={exc} "
            f"waypoint={getattr(wp, 'waypoint_id', 'none')} "
            f"attempts_used={getattr(getattr(ctx, 'cavebot', None), 'gate_attempts_used', -1) if ctx is not None else -1} "
            f"inputs_sent={getattr(getattr(ctx, 'cavebot', None), 'gate_inputs_sent', -1) if ctx is not None else -1}"
        )
        write_fatal(msg, exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1
