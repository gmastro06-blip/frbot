from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_pair
from diagnostics.jsonlog import log as log_json
from diagnostics.last_frames import snapshot
from diagnostics.logger import configure_logger
from diagnostics.schema import base_context_fields
from runtime.env_bootstrap import load_repo_env
from runtime.cavebot_preflight import run as cavebot_preflight_run
from runtime.cavebot_runner import execute_cavebot_tick
from runtime.profile import cap_ticks


_GATE = 'cavebot_full'


load_repo_env()


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None else str(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if name == 'FRBOT_WINDOW_HWND' and raw is not None and str(raw).strip() != '':
        s = str(raw).strip()
        if s.lower().startswith('0x') and len(s) > 2 and set(s[2:].lower()) == {'x'}:
            return int(default)
        try:
            return int(s, 0)
        except Exception as exc:
            raise PreflightFailed('window_hwnd_invalid') from exc
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


def _frames_dir() -> Path:
    raw = (_env_str('FRBOT_REAL_FRAMES_DIR', '') or '').strip()
    if raw:
        return Path(str(raw))
    profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
    if profile == 'prod_emergency':
        return Path('diagnostics') / 'frames_emergency'
    if profile == 'prod_full':
        return Path('diagnostics') / 'frames_full'
    return Path('diagnostics') / 'frames'


def _write_evidence_manifest(*, evidence_dir: Path, capture: object) -> None:
    try:
        src = (_env_str('FRBOT_CAPTURE_SOURCE', 'client') or 'client').strip().lower()
        payload = {
            'capture_source': ('obs_source' if src == 'obs_source' else ('obs' if src == 'obs' else 'client')),
            'obs_source_name': str(getattr(capture, 'obs_source_name', '') or _env_str('FRBOT_OBS_SOURCE_NAME', '') or ''),
            'obs_projector_title': str(_env_str('FRBOT_OBS_PROJECTOR_TITLE', '') or ''),
            **base_context_fields(),
            'ts': int(time.time()),
        }
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / 'evidence_manifest.json').write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    except Exception:
        return


def _write_last_result(
    *,
    evidence_dir: Path,
    ok: bool,
    outcome_kind: str,
    actions_sent: int,
    successes: int,
    before_ppm: str | None,
    after_ppm: str | None,
    evidence_reason: str,
    event_correlation: dict[str, Any] | None = None,
) -> None:
    try:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            'gate': _GATE,
            'ok': bool(ok),
            'outcome_kind': str(outcome_kind),
            'reason': str(outcome_kind),
            'actions_sent': int(actions_sent),
            'inputs_sent': int(actions_sent),
            'successes': int(successes),
            'before_ppm': before_ppm,
            'after_ppm': after_ppm,
            'evidence_reason': str(evidence_reason),
            'evidence_kind': str(evidence_reason),
            'event_correlation': dict(event_correlation or {}),
        }
        (evidence_dir / f'{_GATE}_last_result.json').write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    except Exception:
        return


def _load_cavebot_full_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_CAVEBOT_FULL_BACKEND', _env_str('FRBOT_CAVEBOT_BACKEND', 'real')) or 'real').strip().lower()

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


def run_cavebot_full_only() -> int:
    """cavebot_full gate.

    Evidence-or-abort with semantic minimap progress.
    """

    max_total_ticks = cap_ticks(_env_int('FRBOT_CAVEBOT_FULL_MAX_TICKS', 400))
    evidence_dir = _frames_dir()

    actions_sent = 0

    ctx: RuntimeContext | None = None
    try:
        cfg = _load_cavebot_full_config_from_env()

        if cfg.mode.strip().lower() == 'real' and sys.platform != 'win32':
            write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
            _write_last_result(
                evidence_dir=evidence_dir,
                ok=False,
                outcome_kind='unsupported_platform',
                actions_sent=0,
                successes=0,
                before_ppm=None,
                after_ppm=None,
                evidence_reason='unsupported_platform',
                event_correlation={},
            )
            return 1

        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        capture, input_, binding = cavebot_preflight_run(ctx)
        _write_evidence_manifest(evidence_dir=evidence_dir, capture=capture)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        for tick_index in range(int(max_total_ticks)):
            wp = ctx.cavebot.current_gate_waypoint()
            if wp is None:
                before_ppm, after_ppm = dump_pair(gate=_GATE, before=capture.grab(), after=capture.grab(), reason='route_complete', out_dir=evidence_dir)
                if not before_ppm or not after_ppm:
                    raise PreflightFailed('cavebot_full_missing_evidence_frames')
                _write_last_result(
                    evidence_dir=evidence_dir,
                    ok=True,
                    outcome_kind='cavebot_route_complete',
                    actions_sent=int(actions_sent),
                    successes=1,
                    before_ppm=before_ppm,
                    after_ppm=after_ppm,
                    evidence_reason='route_complete',
                    event_correlation=dict(getattr(getattr(ctx, 'telemetry', object()), 'last_event_correlation', {}) or {}),
                )
                log_json(logger, event='success', gate=_GATE, status='SUCCESS', result='cavebot_route_complete', inputs_sent=int(ctx.cavebot.gate_inputs_sent))
                return 0

            outcome = execute_cavebot_tick(
                ctx,
                capture=capture,
                input_=input_,
                binding=binding,
                waypoint=wp,
                tick_index=int(tick_index),
                gate=_GATE,
            )

            actions_sent = int(ctx.cavebot.gate_inputs_sent)

            log_json(
                logger,
                event='tick',
                gate=_GATE,
                tick_index=int(tick_index),
                waypoint_id=str(wp.waypoint_id),
                attempts_used=int(ctx.cavebot.gate_attempts_used),
                inputs_sent=int(ctx.cavebot.gate_inputs_sent),
                abort_reason=str(outcome.abort_reason or 'none'),
            )

            ctx.telemetry.tick_count += 1

            if outcome.abort_reason is not None:
                raise PreflightFailed(str(outcome.abort_reason))

            if outcome.reached_waypoint:
                ctx.cavebot.gate_waypoint_index += 1
                ctx.cavebot.gate_attempts_used = 0
                ctx.cavebot.gate_ticks_in_waypoint = 0
                ctx.cavebot.gate_reach_streak = 0
                ctx.cavebot_gate.telemetry.last_n_distances = []

        raise PreflightFailed('cavebot_waypoint_stuck')

    except PreflightFailed as exc:
        before, after = snapshot(_GATE)
        before_ppm, after_ppm = dump_pair(gate=_GATE, before=before, after=after, reason=str(exc), out_dir=evidence_dir)
        _write_last_result(
            evidence_dir=evidence_dir,
            ok=False,
            outcome_kind='failed',
            actions_sent=int(actions_sent),
            successes=0,
            before_ppm=before_ppm,
            after_ppm=after_ppm,
            evidence_reason=str(exc),
            event_correlation=(dict(getattr(getattr(ctx, 'telemetry', object()), 'last_event_correlation', {}) or {}) if ctx is not None else {}),
        )
        write_fatal(str(exc), exc)
        return 1
    except ContractViolation as exc:
        write_fatal('runtime crashed', exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1
