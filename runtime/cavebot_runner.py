from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from contracts.capture import CaptureAdapter, Frame
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import RuntimeContext, Waypoint
from contracts.window import WindowBindingAdapter
from diagnostics.last_frames import record_after, record_before
from diagnostics.frame_dump import dump_enabled, dump_pair
from runtime.cavebot_semantics import ProgressResult, compute_progress, detect_player_marker, is_progress_valid


@dataclass(frozen=True, slots=True)
class CavebotTickEvidence:
    marker_before: Optional[object]
    marker_after: Optional[object]
    progress: Optional[ProgressResult]
    status: str


@dataclass(frozen=True, slots=True)
class CavebotTickOutcome:
    reached_waypoint: bool
    evidence: CavebotTickEvidence
    abort_reason: Optional[str]
    event: Optional[str] = None


def _waypoint_distance_px(marker: Optional[object], waypoint: Waypoint) -> float:
    if marker is None:
        return 1e9
    mx = int(getattr(marker, 'x_px', -1))
    my = int(getattr(marker, 'y_px', -1))
    dx = float(mx - int(waypoint.x))
    dy = float(my - int(waypoint.y))
    return float((dx * dx + dy * dy) ** 0.5)


def _select_key_toward_waypoint(marker_before: object, waypoint: Waypoint) -> str:
    mx = int(getattr(marker_before, 'x_px', 0))
    my = int(getattr(marker_before, 'y_px', 0))
    dx = int(waypoint.x) - int(mx)
    dy = int(waypoint.y) - int(my)

    # Prefer dominant axis deterministically.
    if abs(dx) >= abs(dy):
        return 'RIGHT' if dx > 0 else 'LEFT'
    return 'DOWN' if dy > 0 else 'UP'


def _append_trace(*, gate: str, payload: dict) -> None:
    if not dump_enabled():
        return
    try:
        out_dir = Path('diagnostics') / 'frames'
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f'{str(gate).strip().lower()}_trace.jsonl'
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(payload, sort_keys=True) + '\n')
    except Exception:
        return


def _progress_from_frames(ctx: RuntimeContext, before_f: Frame, after_f: Frame, waypoint: Waypoint) -> tuple[Optional[ProgressResult], str]:
    marker_before = detect_player_marker(
        before_f,
        marker_rgb=_parse_rgb(ctx.config.player_marker_rgb),
        tol=int(ctx.config.player_marker_tol),
        min_pixels=int(ctx.config.player_marker_min_pixels),
        max_pixels=int(ctx.config.player_marker_max_pixels),
    )
    if marker_before is None:
        return None, 'cavebot_marker_not_found'

    marker_after = detect_player_marker(
        after_f,
        marker_rgb=_parse_rgb(ctx.config.player_marker_rgb),
        tol=int(ctx.config.player_marker_tol),
        min_pixels=int(ctx.config.player_marker_min_pixels),
        max_pixels=int(ctx.config.player_marker_max_pixels),
    )
    if marker_after is None:
        return None, 'cavebot_marker_not_found'

    progress = compute_progress(marker_before, marker_after, waypoint)

    # Aborts are decided in the runner (evidence-or-abort).
    if float(progress.angle_deg) > 90.0:
        return progress, 'cavebot_wrong_direction'
    if not is_progress_valid(progress, waypoint):
        return progress, 'cavebot_no_progress'
    return progress, 'ok'


def _parse_rgb(raw: str) -> tuple[int, int, int]:
    s = (raw or '').strip()
    parts = [p.strip() for p in s.split(',') if p.strip()]
    if len(parts) != 3:
        return (255, 0, 255)
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return (255, 0, 255)


def execute_cavebot_tick(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    waypoint: Waypoint,
    tick_index: int,
) -> CavebotTickOutcome:
    """Execute exactly one cavebot tick.

    Returns True only when the final waypoint is reached with valid progress.
    """

    # Enforce strong binding before ANY capture/input.
    try:
        binding.assert_bound()
    except Exception as exc:
        raise PreflightFailed('cavebot_window_binding_lost') from exc

    before = capture.grab()
    record_before('cavebot', before)

    marker_before = detect_player_marker(
        before,
        marker_rgb=_parse_rgb(ctx.config.player_marker_rgb),
        tol=int(ctx.config.player_marker_tol),
        min_pixels=int(ctx.config.player_marker_min_pixels),
        max_pixels=int(ctx.config.player_marker_max_pixels),
    )
    if marker_before is None:
        after = capture.grab()
        record_after('cavebot', after)
        marker_after = detect_player_marker(
            after,
            marker_rgb=_parse_rgb(ctx.config.player_marker_rgb),
            tol=int(ctx.config.player_marker_tol),
            min_pixels=int(ctx.config.player_marker_min_pixels),
            max_pixels=int(ctx.config.player_marker_max_pixels),
        )
        ev = CavebotTickEvidence(marker_before=None, marker_after=marker_after, progress=None, status='cavebot_marker_not_found')
        out = CavebotTickOutcome(reached_waypoint=False, evidence=ev, abort_reason='cavebot_marker_not_found')
        if dump_enabled():
            dump_pair(gate='cavebot', before=before, after=after, reason=str(out.abort_reason))
        _append_trace(
            gate='cavebot',
            payload={
                'event': 'abort',
                'tick_index': int(tick_index),
                'abort_reason': str(out.abort_reason),
                'waypoint': {
                    'waypoint_id': str(waypoint.waypoint_id),
                    'x': int(waypoint.x),
                    'y': int(waypoint.y),
                    'z': int(waypoint.z),
                    'radius_px': int(waypoint.radius_px),
                    'max_ticks': int(waypoint.max_ticks),
                },
            },
        )
        return out

    # Waypoint timeout is deterministic (no further input).
    if int(ctx.cavebot.gate_ticks_in_waypoint) >= int(waypoint.max_ticks):
        after = capture.grab()
        record_after('cavebot', after)
        ev = CavebotTickEvidence(marker_before=marker_before, marker_after=None, progress=None, status='cavebot_waypoint_timeout')
        out = CavebotTickOutcome(reached_waypoint=False, evidence=ev, abort_reason='cavebot_waypoint_timeout')
        if dump_enabled():
            dump_pair(gate='cavebot', before=before, after=after, reason=str(out.abort_reason))
        _append_trace(
            gate='cavebot',
            payload={
                'event': 'abort',
                'tick_index': int(tick_index),
                'abort_reason': str(out.abort_reason),
                'waypoint': {
                    'waypoint_id': str(waypoint.waypoint_id),
                    'x': int(waypoint.x),
                    'y': int(waypoint.y),
                    'z': int(waypoint.z),
                    'radius_px': int(waypoint.radius_px),
                    'max_ticks': int(waypoint.max_ticks),
                },
            },
        )
        return out

    # Arrival stability check: reached only if distance <= radius_px for >=2 consecutive ticks.
    dist_before = _waypoint_distance_px(marker_before, waypoint)
    if float(dist_before) <= float(waypoint.radius_px):
        ctx.cavebot.gate_reach_streak = int(ctx.cavebot.gate_reach_streak) + 1
        after = capture.grab()
        record_after('cavebot', after)
        marker_after = detect_player_marker(
            after,
            marker_rgb=_parse_rgb(ctx.config.player_marker_rgb),
            tol=int(ctx.config.player_marker_tol),
            min_pixels=int(ctx.config.player_marker_min_pixels),
            max_pixels=int(ctx.config.player_marker_max_pixels),
        )
        dist_after = _waypoint_distance_px(marker_after, waypoint)
        ctx.cavebot_gate.telemetry.last_n_distances.append(float(dist_after))
        if len(ctx.cavebot_gate.telemetry.last_n_distances) > 50:
            ctx.cavebot_gate.telemetry.last_n_distances = ctx.cavebot_gate.telemetry.last_n_distances[-50:]

        reached_now = int(ctx.cavebot.gate_reach_streak) >= 2 and float(dist_after) <= float(waypoint.radius_px)
        ev = CavebotTickEvidence(marker_before=marker_before, marker_after=marker_after, progress=None, status='ok')
        out = CavebotTickOutcome(reached_waypoint=bool(reached_now), evidence=ev, abort_reason=None)
        if reached_now:
            out = CavebotTickOutcome(reached_waypoint=True, evidence=ev, abort_reason=None, event='WAYPOINT_REACHED')
        ctx.cavebot.gate_ticks_in_waypoint += 1
        _append_trace(
            gate='cavebot',
            payload={
                'event': str(out.event or 'tick'),
                'tick_index': int(tick_index),
                'input_sent': False,
                'key': '',
                'reach_streak': int(ctx.cavebot.gate_reach_streak),
                'distance_before_px': float(dist_before),
                'distance_after_px': float(dist_after),
                'angle_deg': 0.0,
                'abort_reason': 'none',
                'waypoint': {
                    'waypoint_id': str(waypoint.waypoint_id),
                    'x': int(waypoint.x),
                    'y': int(waypoint.y),
                    'z': int(waypoint.z),
                    'radius_px': int(waypoint.radius_px),
                    'max_ticks': int(waypoint.max_ticks),
                },
            },
        )
        return out

    # Not yet reached: select exactly one input (intent) per tick.
    ctx.cavebot.gate_reach_streak = 0
    key = _select_key_toward_waypoint(marker_before, waypoint)

    # Enforce strong binding immediately before input.
    try:
        binding.assert_bound()
    except Exception as exc:
        raise PreflightFailed('cavebot_window_binding_lost') from exc

    input_.press_key(str(key))
    ctx.cavebot.gate_inputs_sent += 1

    after = capture.grab()
    record_after('cavebot', after)

    progress, status = _progress_from_frames(ctx, before, after, waypoint)

    marker_after = detect_player_marker(
        after,
        marker_rgb=_parse_rgb(ctx.config.player_marker_rgb),
        tol=int(ctx.config.player_marker_tol),
        min_pixels=int(ctx.config.player_marker_min_pixels),
        max_pixels=int(ctx.config.player_marker_max_pixels),
    )

    evidence = CavebotTickEvidence(
        marker_before=marker_before,
        marker_after=marker_after,
        progress=progress,
        status=str(status),
    )

    # Update telemetry distances (post-input).
    dist_b = float(progress.distance_before_px) if progress is not None else 1e9
    dist_a = float(progress.distance_after_px) if progress is not None else 1e9
    ctx.cavebot_gate.telemetry.distance_before_px = float(dist_b)
    ctx.cavebot_gate.telemetry.distance_after_px = float(dist_a)
    ctx.cavebot_gate.telemetry.angle_deg = float(progress.angle_deg) if progress is not None else 0.0
    ctx.cavebot_gate.telemetry.attempts_used = int(ctx.cavebot.gate_attempts_used)
    ctx.cavebot_gate.telemetry.inputs_sent = int(ctx.cavebot.gate_inputs_sent)
    ctx.cavebot_gate.telemetry.waypoint_id = str(waypoint.waypoint_id)
    ctx.cavebot_gate.telemetry.last_n_distances.append(float(dist_a))
    if len(ctx.cavebot_gate.telemetry.last_n_distances) > 50:
        ctx.cavebot_gate.telemetry.last_n_distances = ctx.cavebot_gate.telemetry.last_n_distances[-50:]

    _append_trace(
        gate='cavebot',
        payload={
            'event': 'tick',
            'tick_index': int(tick_index),
            'input_sent': True,
            'key': str(key),
            'reach_streak': int(ctx.cavebot.gate_reach_streak),
            'distance_before_px': float(dist_b),
            'distance_after_px': float(dist_a),
            'angle_deg': float(progress.angle_deg) if progress is not None else 0.0,
            'abort_reason': 'none',
            'waypoint': {
                'waypoint_id': str(waypoint.waypoint_id),
                'x': int(waypoint.x),
                'y': int(waypoint.y),
                'z': int(waypoint.z),
                'radius_px': int(waypoint.radius_px),
                'max_ticks': int(waypoint.max_ticks),
            },
        },
    )

    if status == 'cavebot_marker_not_found':
        out = CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason='cavebot_marker_not_found')
        if dump_enabled():
            dump_pair(gate='cavebot', before=before, after=after, reason=str(out.abort_reason))
        _append_trace(
            gate='cavebot',
            payload={
                'event': 'abort',
                'tick_index': int(tick_index),
                'abort_reason': str(out.abort_reason),
                'waypoint': {
                    'waypoint_id': str(waypoint.waypoint_id),
                    'x': int(waypoint.x),
                    'y': int(waypoint.y),
                    'z': int(waypoint.z),
                    'radius_px': int(waypoint.radius_px),
                    'max_ticks': int(waypoint.max_ticks),
                },
            },
        )
        return out

    if status == 'cavebot_wrong_direction':
        out = CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason='cavebot_wrong_direction')
        if dump_enabled():
            dump_pair(gate='cavebot', before=before, after=after, reason=str(out.abort_reason))
        _append_trace(
            gate='cavebot',
            payload={
                'event': 'abort',
                'tick_index': int(tick_index),
                'abort_reason': str(out.abort_reason),
                'waypoint': {
                    'waypoint_id': str(waypoint.waypoint_id),
                    'x': int(waypoint.x),
                    'y': int(waypoint.y),
                    'z': int(waypoint.z),
                    'radius_px': int(waypoint.radius_px),
                    'max_ticks': int(waypoint.max_ticks),
                },
            },
        )
        return out

    if status == 'cavebot_no_progress':
        # No semantic progress: allow bounded retries, then deterministically abort stuck.
        # Stuck detection criteria over last N distances:
        # - no decreases at all (flat/increasing), OR
        # - oscillation (at least one decrease and at least one increase)
        N = 5
        distances = list(ctx.cavebot_gate.telemetry.last_n_distances)
        last_n = distances[-N:] if len(distances) >= N else []
        if len(last_n) >= N:
            eps = 1e-6
            dec = any((last_n[i] + eps) < last_n[i - 1] for i in range(1, N))
            inc = any((last_n[i] - eps) > last_n[i - 1] for i in range(1, N))
            if (not dec) or (dec and inc):
                out = CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason='cavebot_stuck_detected')
                if dump_enabled():
                    dump_pair(gate='cavebot', before=before, after=after, reason=str(out.abort_reason))
                _append_trace(
                    gate='cavebot',
                    payload={
                        'event': 'abort',
                        'tick_index': int(tick_index),
                        'abort_reason': str(out.abort_reason),
                        'waypoint': {
                            'waypoint_id': str(waypoint.waypoint_id),
                            'x': int(waypoint.x),
                            'y': int(waypoint.y),
                            'z': int(waypoint.z),
                            'radius_px': int(waypoint.radius_px),
                            'max_ticks': int(waypoint.max_ticks),
                        },
                    },
                )
                return out

        # Not stuck yet: consume tick and allow retry.
        ctx.cavebot.gate_ticks_in_waypoint += 1
        ctx.cavebot.gate_attempts_used += 1
        return CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason=None)

    # status == ok
    ctx.cavebot.gate_ticks_in_waypoint += 1
    ctx.cavebot.gate_attempts_used = 0

    return CavebotTickOutcome(
        reached_waypoint=False,
        evidence=evidence,
        abort_reason=None,
    )
