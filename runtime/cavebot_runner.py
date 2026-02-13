from __future__ import annotations

import json
import os
import time

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from contracts.capture import CaptureAdapter, Frame
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import MinimapMarker, RuntimeContext, Waypoint
from contracts.window import WindowBindingAdapter
from diagnostics.last_frames import record_after, record_before
from diagnostics.frame_dump import dump_enabled, dump_pair
from runtime.cavebot_semantics import ProgressResult, compute_progress, detect_player_marker, is_progress_valid, select_player_marker
from runtime.pacing import wait_until_ns
from runtime.profile import is_prod_emergency
from runtime.event_correlation import attach_snapshot, new_event, validate


def _estimate_minimap_translation_px(
    before_f: Frame,
    after_f: Frame,
    *,
    max_shift_px: int = 6,
    sample_step_px: int = 4,
) -> tuple[int, int, float, float]:
    """Estimate translation between minimap crops (in pixels).

    Returns (dx, dy, sad_best, sad_0).

    Convention: dx,dy are defined so that after(x,y) ~= before(x+dx, y+dy).
    This means a positive dx implies minimap content moved left (player moved right).
    """

    if not bool(before_f.minimap_detected and after_f.minimap_detected):
        return (0, 0, 0.0, 0.0)

    w = int(before_f.minimap_width)
    h = int(before_f.minimap_height)
    if w <= 0 or h <= 0:
        return (0, 0, 0.0, 0.0)
    if int(after_f.minimap_width) != w or int(after_f.minimap_height) != h:
        return (0, 0, 0.0, 0.0)

    b = bytes(before_f.minimap_rgb)
    a = bytes(after_f.minimap_rgb)
    if not b or not a:
        return (0, 0, 0.0, 0.0)
    if len(b) != (w * h * 3) or len(a) != (w * h * 3):
        return (0, 0, 0.0, 0.0)

    max_s = max(0, int(max_shift_px))
    step = max(1, int(sample_step_px))

    def sad_for(dx: int, dy: int) -> float:
        x0 = max(0, -int(dx))
        x1 = min(w, w - int(dx))
        y0 = max(0, -int(dy))
        y1 = min(h, h - int(dy))
        if x1 <= x0 or y1 <= y0:
            return 1e30

        sad = 0.0
        n = 0
        for y in range(int(y0), int(y1), int(step)):
            row_a = int(y) * int(w)
            row_b = int(y + int(dy)) * int(w)
            for x in range(int(x0), int(x1), int(step)):
                ia = (row_a + int(x)) * 3
                ib = (row_b + int(x + int(dx))) * 3
                sad += abs(int(a[ia]) - int(b[ib]))
                sad += abs(int(a[ia + 1]) - int(b[ib + 1]))
                sad += abs(int(a[ia + 2]) - int(b[ib + 2]))
                n += 1
        if n <= 0:
            return 1e30
        return float(sad) / float(n)

    sad_0 = sad_for(0, 0)
    best_dx = 0
    best_dy = 0
    best_sad = float(sad_0)

    for dy in range(-max_s, max_s + 1):
        for dx in range(-max_s, max_s + 1):
            if dx == 0 and dy == 0:
                continue
            s = sad_for(int(dx), int(dy))
            if float(s) < float(best_sad):
                best_sad = float(s)
                best_dx = int(dx)
                best_dy = int(dy)

    return (int(best_dx), int(best_dy), float(best_sad), float(sad_0))


@dataclass(frozen=True, slots=True)
class CavebotTickEvidence:
    marker_before: Optional[MinimapMarker]
    marker_after: Optional[MinimapMarker]
    progress: Optional[ProgressResult]
    status: str


@dataclass(frozen=True, slots=True)
class CavebotTickOutcome:
    reached_waypoint: bool
    evidence: CavebotTickEvidence
    abort_reason: Optional[str]
    event: Optional[str] = None


@dataclass(frozen=True, slots=True)
class CavebotProgressEval:
    progress: Optional[ProgressResult]
    status: str
    marker_after: Optional[MinimapMarker]
    sel_after_confidence: float
    sel_after_candidate_id: Optional[int]
    sel_after_candidates: tuple[object, ...]
    sel_after_details: dict[str, object]
    inferred_dx: int
    inferred_dy: int
    inferred_sad_best: float
    inferred_sad_0: float


def _waypoint_distance_px(marker: Optional[MinimapMarker], waypoint: Waypoint) -> float:
    if marker is None:
        return 1e9
    mx = int(getattr(marker, 'x_px', -1))
    my = int(getattr(marker, 'y_px', -1))
    dx = float(mx - int(waypoint.x))
    dy = float(my - int(waypoint.y))
    return float((dx * dx + dy * dy) ** 0.5)


def _wrong_direction_threshold_deg(*, real_mode: bool) -> float:
    if not bool(real_mode):
        return 90.0
    raw = str(os.environ.get('FRBOT_CAVEBOT_WRONG_DIRECTION_ANGLE_DEG') or '').strip()
    if not raw:
        return 90.0
    try:
        val = float(raw)
    except Exception:
        return 90.0
    if val < 90.0:
        return 90.0
    if val > 180.0:
        return 180.0
    return float(val)


def _wrong_direction_abort_streak(*, real_mode: bool) -> int:
    if not bool(real_mode):
        return 1
    raw = str(os.environ.get('FRBOT_CAVEBOT_WRONG_DIRECTION_ABORT_STREAK') or '').strip()
    if not raw:
        return 1
    try:
        val = int(raw)
    except Exception:
        return 1
    return max(1, int(val))


def _dead_reckon_on_static(*, real_mode: bool) -> bool:
    if not bool(real_mode):
        return False
    raw = str(os.environ.get('FRBOT_CAVEBOT_DEAD_RECKON_ON_STATIC') or '').strip().lower()
    return raw in {'1', 'true', 'yes', 'on'}


def _dead_reckon_step_px(*, real_mode: bool) -> int:
    if not bool(real_mode):
        return 1
    raw = str(os.environ.get('FRBOT_CAVEBOT_DEAD_RECKON_STEP_PX') or '').strip()
    if not raw:
        return 1
    try:
        val = int(raw)
    except Exception:
        return 1
    return max(1, min(int(val), 4))


def _stuck_window(*, real_mode: bool) -> int:
    raw = str(os.environ.get('FRBOT_CAVEBOT_STUCK_WINDOW') or '').strip()
    if not raw:
        return 5
    try:
        val = int(raw)
    except Exception:
        return 5
    max_w = 30 if bool(real_mode) else 15
    return max(3, min(int(val), int(max_w)))


def _select_key_toward_waypoint(marker_before: object, waypoint: Waypoint) -> str:
    mx = int(getattr(marker_before, 'x_px', 0))
    my = int(getattr(marker_before, 'y_px', 0))
    dx = int(waypoint.x) - int(mx)
    dy = int(waypoint.y) - int(my)

    # Prefer dominant axis deterministically.
    if abs(dx) >= abs(dy):
        return 'RIGHT' if dx > 0 else 'LEFT'
    return 'DOWN' if dy > 0 else 'UP'


def _frames_dir() -> Path:
    raw = str(os.environ.get('FRBOT_REAL_FRAMES_DIR') or '').strip()
    if raw:
        return Path(raw)
    prof = str(os.environ.get('FRBOT_PROFILE') or '').strip().lower()
    if prof == 'prod_emergency':
        return Path('diagnostics') / 'frames_emergency'
    if prof == 'prod_full':
        return Path('diagnostics') / 'frames_full'
    return Path('diagnostics') / 'frames'


def _append_trace(*, gate: str, payload: dict) -> None:
    try:
        # Required audit trace (generated artifact; must not be committed).
        out_dir = _frames_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        g = str(gate).strip().lower()
        # Keep the canonical artifact name for the cavebot semantic trace.
        # (Multiple gates may reuse the runner, but inventory/auditors expect this file.)
        if g.startswith('cavebot'):
            path = out_dir / 'cavebot_trace.jsonl'
        else:
            path = out_dir / f'{g}_trace.jsonl'
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(payload, sort_keys=True) + '\n')
    except Exception:
        return


def _dump_marker_abort_pair(*, gate: str, before: Frame | None, after: Frame | None, reason: str) -> None:
    # PROD_EMERGENCY REAL contract: marker aborts must dump BEFORE/AFTER.
    try:
        dump_pair(gate=str(gate), before=before, after=after, reason=str(reason))
    except Exception:
        return


def _progress_from_frames(ctx: RuntimeContext, before_f: Frame, after_f: Frame, waypoint: Waypoint) -> CavebotProgressEval:
    cfg_rgb = _marker_rgb(ctx)
    sel_b = select_player_marker(
        before_f,
        marker_rgb=cfg_rgb,
        tol=int(ctx.config.player_marker_tol),
        min_pixels=int(ctx.config.player_marker_min_pixels),
        max_pixels=int(ctx.config.player_marker_max_pixels),
        prev_marker=getattr(ctx.cavebot_gate.telemetry, 'marker_after', None),
    )
    if sel_b.abort_reason is not None:
        return CavebotProgressEval(
            progress=None,
            status=str(sel_b.abort_reason),
            marker_after=None,
            sel_after_confidence=0.0,
            sel_after_candidate_id=None,
            sel_after_candidates=(),
            sel_after_details={},
            inferred_dx=0,
            inferred_dy=0,
            inferred_sad_best=0.0,
            inferred_sad_0=0.0,
        )
    marker_before = sel_b.marker
    if marker_before is None:
        return CavebotProgressEval(
            progress=None,
            status='cavebot_marker_not_found',
            marker_after=None,
            sel_after_confidence=0.0,
            sel_after_candidate_id=None,
            sel_after_candidates=(),
            sel_after_details={},
            inferred_dx=0,
            inferred_dy=0,
            inferred_sad_best=0.0,
            inferred_sad_0=0.0,
        )

    sel_a = select_player_marker(
        after_f,
        marker_rgb=cfg_rgb,
        tol=int(ctx.config.player_marker_tol),
        min_pixels=int(ctx.config.player_marker_min_pixels),
        max_pixels=int(ctx.config.player_marker_max_pixels),
        prev_marker=marker_before,
    )
    if sel_a.abort_reason is not None:
        return CavebotProgressEval(
            progress=None,
            status=str(sel_a.abort_reason),
            marker_after=sel_a.marker,
            sel_after_confidence=float(sel_a.confidence),
            sel_after_candidate_id=sel_a.selected_candidate_id,
            sel_after_candidates=tuple(sel_a.candidates),
            sel_after_details=dict(sel_a.details),
            inferred_dx=0,
            inferred_dy=0,
            inferred_sad_best=0.0,
            inferred_sad_0=0.0,
        )
    marker_after = sel_a.marker
    if marker_after is None:
        return CavebotProgressEval(
            progress=None,
            status='cavebot_marker_not_found',
            marker_after=None,
            sel_after_confidence=float(sel_a.confidence),
            sel_after_candidate_id=sel_a.selected_candidate_id,
            sel_after_candidates=tuple(sel_a.candidates),
            sel_after_details=dict(sel_a.details),
            inferred_dx=0,
            inferred_dy=0,
            inferred_sad_best=0.0,
            inferred_sad_0=0.0,
        )

    # Scroll-based inference is ONLY for REAL mode. Mock world already provides
    # deterministic moving markers and should not be influenced by translation heuristics.
    real_mode = str(getattr(ctx.config, 'mode', '')).strip().lower() == 'real'

    # If the marker is stable/centered (common in real minimaps), infer movement from
    # minimap scroll (translation) and accumulate it in telemetry to avoid false "no progress".
    min_delta = int(getattr(ctx.config, 'cavebot_min_pixel_delta', 2) or 2)
    dx_m = int(getattr(marker_after, 'x_px', 0)) - int(getattr(marker_before, 'x_px', 0))
    dy_m = int(getattr(marker_after, 'y_px', 0)) - int(getattr(marker_before, 'y_px', 0))

    tel = getattr(getattr(ctx, 'cavebot_gate', None), 'telemetry', None)
    base_x = int(getattr(marker_before, 'x_px', 0))
    base_y = int(getattr(marker_before, 'y_px', 0))
    try:
        if tel is not None and getattr(tel, 'virtual_x_px', None) is not None:
            base_x = int(getattr(tel, 'virtual_x_px'))
        if tel is not None and getattr(tel, 'virtual_y_px', None) is not None:
            base_y = int(getattr(tel, 'virtual_y_px'))
    except Exception:
        base_x = int(getattr(marker_before, 'x_px', 0))
        base_y = int(getattr(marker_before, 'y_px', 0))

    try:
        virt_before = type(marker_before)(x_px=int(base_x), y_px=int(base_y), pixel_count=int(getattr(marker_before, 'pixel_count', 0)))
    except Exception:
        virt_before = marker_before

    virt_after = marker_after

    wrong_dir_threshold_deg = _wrong_direction_threshold_deg(real_mode=bool(real_mode))

    inferred_dx = 0
    inferred_dy = 0
    inferred_sad_best = 0.0
    inferred_sad_0 = 0.0

    if not bool(real_mode):
        # In non-real mode, marker coordinates are authoritative.
        try:
            if tel is not None:
                tel.virtual_x_px = int(getattr(marker_after, 'x_px', 0))
                tel.virtual_y_px = int(getattr(marker_after, 'y_px', 0))
        except Exception:
            pass
        progress = compute_progress(marker_before, marker_after, waypoint)
        status = 'ok'
        if float(progress.angle_deg) > float(wrong_dir_threshold_deg):
            status = 'cavebot_wrong_direction'
        elif not is_progress_valid(progress, waypoint):
            status = 'cavebot_no_progress'
        return CavebotProgressEval(
            progress=progress,
            status=str(status),
            marker_after=marker_after,
            sel_after_confidence=float(sel_a.confidence),
            sel_after_candidate_id=sel_a.selected_candidate_id,
            sel_after_candidates=tuple(sel_a.candidates),
            sel_after_details=dict(sel_a.details),
            inferred_dx=int(inferred_dx),
            inferred_dy=int(inferred_dy),
            inferred_sad_best=float(inferred_sad_best),
            inferred_sad_0=float(inferred_sad_0),
        )

    # Primary: if the marker itself moves, trust it and keep telemetry in sync.
    if (abs(int(dx_m)) + abs(int(dy_m))) >= int(max(1, min_delta)):
        virt_after = marker_after
        try:
            if tel is not None:
                tel.virtual_x_px = int(getattr(marker_after, 'x_px', 0))
                tel.virtual_y_px = int(getattr(marker_after, 'y_px', 0))
        except Exception:
            pass
    else:
        # Fallback: infer minimap scroll and accumulate virtual position.
        dx_s, dy_s, sad_best, sad_0 = _estimate_minimap_translation_px(before_f, after_f)
        inferred_dx = int(dx_s)
        inferred_dy = int(dy_s)
        inferred_sad_best = float(sad_best)
        inferred_sad_0 = float(sad_0)
        improved = (float(sad_best) + 0.25) < float(sad_0)
        if improved and (abs(int(dx_s)) + abs(int(dy_s))) >= int(max(1, min_delta)):
            new_x = int(base_x) + int(dx_s)
            new_y = int(base_y) + int(dy_s)
            try:
                virt_after = type(marker_before)(x_px=int(new_x), y_px=int(new_y), pixel_count=int(getattr(marker_after, 'pixel_count', 0)))
            except Exception:
                virt_after = marker_after
            try:
                if tel is not None:
                    tel.virtual_x_px = int(new_x)
                    tel.virtual_y_px = int(new_y)
            except Exception:
                pass
        else:
            # No evidence of movement: keep virtual marker unchanged.
            virt_after = virt_before

    progress = compute_progress(virt_before, virt_after, waypoint)

    # Aborts are decided in the runner (evidence-or-abort).
    status = 'ok'
    if float(progress.angle_deg) > float(wrong_dir_threshold_deg):
        status = 'cavebot_wrong_direction'
    elif not is_progress_valid(progress, waypoint):
        status = 'cavebot_no_progress'
    return CavebotProgressEval(
        progress=progress,
        status=str(status),
        marker_after=marker_after,
        sel_after_confidence=float(sel_a.confidence),
        sel_after_candidate_id=sel_a.selected_candidate_id,
        sel_after_candidates=tuple(sel_a.candidates),
        sel_after_details=dict(sel_a.details),
        inferred_dx=int(inferred_dx),
        inferred_dy=int(inferred_dy),
        inferred_sad_best=float(inferred_sad_best),
        inferred_sad_0=float(inferred_sad_0),
    )


def _parse_rgb(raw: str) -> tuple[int, int, int]:
    s = (raw or '').strip()
    parts = [p.strip() for p in s.split(',') if p.strip()]
    if len(parts) != 3:
        return (255, 0, 255)
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return (255, 0, 255)


def _marker_rgb(ctx: RuntimeContext) -> tuple[int, int, int]:
    try:
        tel = getattr(getattr(ctx, 'cavebot_gate', None), 'telemetry', None)
        rgb = getattr(tel, 'marker_rgb', None)
        if isinstance(rgb, (tuple, list)) and len(rgb) == 3:
            return (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    except Exception:
        pass
    return _parse_rgb(ctx.config.player_marker_rgb)


def execute_cavebot_tick(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    waypoint: Waypoint,
    tick_index: int,
    gate: str = 'cavebot',
) -> CavebotTickOutcome:
    """Execute exactly one cavebot tick.

    Returns True only when the final waypoint is reached with valid progress.
    """

    # Enforce strong binding before ANY capture/input.
    try:
        binding.assert_bound()
    except Exception as exc:
        raise PreflightFailed('cavebot_window_binding_lost') from exc

    event = new_event(
        gate=str(gate),
        intent={
            'type': 'cavebot_tick',
            'waypoint_id': str(getattr(waypoint, 'waypoint_id', '') or ''),
            'tick_index': int(tick_index),
        },
    )

    before_ts_ns = int(time.monotonic_ns())
    attach_snapshot(event, stage='before', ts_ns=before_ts_ns, status=binding.snapshot())
    before = capture.grab()
    record_before(str(gate), before)

    cfg_rgb = _marker_rgb(ctx)
    prev_marker = getattr(ctx.cavebot_gate.telemetry, 'marker_after', None)
    sel_before = select_player_marker(
        before,
        marker_rgb=cfg_rgb,
        tol=int(ctx.config.player_marker_tol),
        min_pixels=int(ctx.config.player_marker_min_pixels),
        max_pixels=int(ctx.config.player_marker_max_pixels),
        prev_marker=prev_marker,
    )

    marker_before = sel_before.marker

    # Marker aborts: must be explicit with evidence.
    if sel_before.abort_reason in {'cavebot_marker_ambiguous', 'cavebot_marker_roi_black'}:
        after = capture.grab()
        record_after(str(gate), after)

        ev = CavebotTickEvidence(
            marker_before=None,
            marker_after=None,
            progress=None,
            status=str(sel_before.abort_reason),
        )
        out = CavebotTickOutcome(reached_waypoint=False, evidence=ev, abort_reason=str(sel_before.abort_reason))
        if is_prod_emergency() and str(ctx.config.mode).strip().lower() == 'real':
            _dump_marker_abort_pair(gate=str(gate), before=before, after=after, reason=str(out.abort_reason))
        elif dump_enabled():
            dump_pair(gate=str(gate), before=before, after=after, reason=str(out.abort_reason))

        _append_trace(
            gate=str(gate),
            payload={
                'event': 'abort',
                'tick_index': int(tick_index),
                'abort_reason': str(out.abort_reason),
                'candidates_count': int(len(sel_before.candidates)),
                'selected_marker_confidence': float(sel_before.confidence),
                'selected_marker_id': sel_before.selected_candidate_id,
                'marker_candidates': sel_before.details.get('marker_candidates', []),
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
        # Attach structured details for fatal.log.
        abort_exc = PreflightFailed(str(out.abort_reason))
        setattr(
            abort_exc,
            'details',
            {
                'reason': str(out.abort_reason),
                'marker_candidates': sel_before.details.get('marker_candidates', []),
                'selected_marker': None,
                'selected_marker_id': sel_before.selected_candidate_id,
                'selected_marker_confidence': float(sel_before.confidence),
                'luma': {k: sel_before.details.get(k) for k in ['full_std_luma', 'roi_std_luma'] if k in sel_before.details},
            },
        )
        raise abort_exc

    if marker_before is None:
        after = capture.grab()
        record_after(str(gate), after)
        ev = CavebotTickEvidence(marker_before=None, marker_after=None, progress=None, status='cavebot_marker_not_found')
        out = CavebotTickOutcome(reached_waypoint=False, evidence=ev, abort_reason='cavebot_marker_not_found')
        if is_prod_emergency() and str(ctx.config.mode).strip().lower() == 'real':
            _dump_marker_abort_pair(gate=str(gate), before=before, after=after, reason=str(out.abort_reason))
        elif dump_enabled():
            dump_pair(gate=str(gate), before=before, after=after, reason=str(out.abort_reason))
        _append_trace(
            gate=str(gate),
            payload={
                'event': 'abort',
                'tick_index': int(tick_index),
                'abort_reason': str(out.abort_reason),
                'candidates_count': int(len(sel_before.candidates)),
                'selected_marker_confidence': float(sel_before.confidence),
                'selected_marker_id': sel_before.selected_candidate_id,
                'marker_candidates': sel_before.details.get('marker_candidates', []),
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

    # Persist marker telemetry for next tick stabilization.
    ctx.cavebot_gate.telemetry.marker_before = marker_before

    # Waypoint timeout is deterministic (no further input).
    if int(ctx.cavebot.gate_ticks_in_waypoint) >= int(waypoint.max_ticks):
        after = capture.grab()
        record_after(str(gate), after)
        ev = CavebotTickEvidence(marker_before=marker_before, marker_after=None, progress=None, status='cavebot_waypoint_timeout')
        out = CavebotTickOutcome(reached_waypoint=False, evidence=ev, abort_reason='cavebot_waypoint_timeout')
        if dump_enabled():
            dump_pair(gate=str(gate), before=before, after=after, reason=str(out.abort_reason))
        _append_trace(
            gate=str(gate),
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
    # Use virtual marker position in REAL mode only (centered minimap scroll inference).
    virt_before = marker_before
    try:
        tel = getattr(getattr(ctx, 'cavebot_gate', None), 'telemetry', None)
        vx = getattr(tel, 'virtual_x_px', None) if tel is not None else None
        vy = getattr(tel, 'virtual_y_px', None) if tel is not None else None
        real_mode = str(getattr(ctx.config, 'mode', '')).strip().lower() == 'real'
        if bool(real_mode) and vx is not None and vy is not None:
            virt_before = type(marker_before)(
                x_px=int(vx),
                y_px=int(vy),
                pixel_count=int(getattr(marker_before, 'pixel_count', 0)),
            )
    except Exception:
        virt_before = marker_before

    dist_before = _waypoint_distance_px(virt_before, waypoint)
    if float(dist_before) <= float(waypoint.radius_px):
        ctx.cavebot.gate_reach_streak = int(ctx.cavebot.gate_reach_streak) + 1
        after = capture.grab()
        record_after(str(gate), after)
        marker_after = detect_player_marker(
            after,
            marker_rgb=_marker_rgb(ctx),
            tol=int(ctx.config.player_marker_tol),
            min_pixels=int(ctx.config.player_marker_min_pixels),
            max_pixels=int(ctx.config.player_marker_max_pixels),
        )
        # While holding position (no input), keep the virtual distance stable.
        dist_after = float(dist_before)
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
            gate=str(gate),
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
    key_marker = marker_before
    try:
        real_mode = str(getattr(ctx.config, 'mode', '')).strip().lower() == 'real'
        tel = getattr(getattr(ctx, 'cavebot_gate', None), 'telemetry', None)
        vx = getattr(tel, 'virtual_x_px', None) if tel is not None else None
        vy = getattr(tel, 'virtual_y_px', None) if tel is not None else None
        if bool(real_mode) and vx is not None and vy is not None:
            key_marker = type(marker_before)(
                x_px=int(vx),
                y_px=int(vy),
                pixel_count=int(getattr(marker_before, 'pixel_count', 0)),
            )
    except Exception:
        key_marker = marker_before
    key = _select_key_toward_waypoint(key_marker, waypoint)

    # Enforce strong binding immediately before input.
    try:
        binding.assert_bound()
    except Exception as exc:
        raise PreflightFailed('cavebot_window_binding_lost') from exc

    input_ts_ns = int(time.monotonic_ns())
    attach_snapshot(event, stage='input', ts_ns=input_ts_ns, status=binding.snapshot())

    input_.press_key(str(key))
    ctx.cavebot.gate_inputs_sent += 1

    # Real UI/minimap may take a moment to reflect movement; OBS screenshots can also lag.
    # Use tick-pacing helper (time.sleep is forbidden by CI guardrails).
    try:
        raw = os.environ.get('FRBOT_POST_MOVE_DELAY_MS', '')
        if str(raw).strip() == '':
            post_move_ms = 200 if str(ctx.config.mode).strip().lower() == 'real' else 0
        else:
            post_move_ms = int(raw)
    except Exception:
        post_move_ms = 200 if str(ctx.config.mode).strip().lower() == 'real' else 0
    if int(post_move_ms) > 0:
        wait_until_ns(int(time.monotonic_ns() + (int(post_move_ms) * 1_000_000)))

    after = capture.grab()
    after_ts_ns = int(time.monotonic_ns())
    attach_snapshot(event, stage='after', ts_ns=after_ts_ns, status=binding.snapshot())
    record_after(str(gate), after)

    corr_ok, corr_reason, corr_details = validate(event)
    event['correlation_ok'] = bool(corr_ok)
    event['correlation_reason'] = str(corr_reason)
    if corr_details:
        event['correlation_details'] = dict(corr_details)
    ctx.telemetry.last_event_correlation = dict(event)
    if not corr_ok:
        corr_exc = PreflightFailed('binding_correlation_failed')
        try:
            setattr(corr_exc, 'details', {'event_correlation': event})
        except Exception:
            pass
        raise corr_exc

    eval_ = _progress_from_frames(ctx, before, after, waypoint)
    progress = eval_.progress
    status = str(eval_.status)

    # Best-effort: inferred minimap scroll reused from progress evaluation.
    inferred_dx = int(eval_.inferred_dx)
    inferred_dy = int(eval_.inferred_dy)
    inferred_sad_best = float(eval_.inferred_sad_best)
    inferred_sad_0 = float(eval_.inferred_sad_0)

    marker_after = eval_.marker_after

    evidence = CavebotTickEvidence(
        marker_before=marker_before,
        marker_after=marker_after,
        progress=progress,
        status=str(status),
    )

    # Persist marker telemetry for next tick stabilization.
    ctx.cavebot_gate.telemetry.marker_after = marker_after

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

    real_mode = str(getattr(ctx.config, 'mode', '')).strip().lower() == 'real'

    if (
        str(status) == 'cavebot_no_progress'
        and bool(real_mode)
        and int(inferred_dx) == 0
        and int(inferred_dy) == 0
        and _dead_reckon_on_static(real_mode=bool(real_mode))
    ):
        step_px = _dead_reckon_step_px(real_mode=bool(real_mode))
        try:
            vx = int(getattr(ctx.cavebot_gate.telemetry, 'virtual_x_px', getattr(marker_before, 'x_px', 0)))
            vy = int(getattr(ctx.cavebot_gate.telemetry, 'virtual_y_px', getattr(marker_before, 'y_px', 0)))
            if str(key) == 'RIGHT':
                vx += int(step_px)
            elif str(key) == 'LEFT':
                vx -= int(step_px)
            elif str(key) == 'DOWN':
                vy += int(step_px)
            elif str(key) == 'UP':
                vy -= int(step_px)
            ctx.cavebot_gate.telemetry.virtual_x_px = int(vx)
            ctx.cavebot_gate.telemetry.virtual_y_px = int(vy)
        except Exception:
            pass

    _append_trace(
        gate=str(gate),
        payload={
            'event': 'tick',
            'tick_index': int(tick_index),
            'input_sent': True,
            'key': str(key),
            'minimap_shift_dx': int(inferred_dx),
            'minimap_shift_dy': int(inferred_dy),
            'minimap_shift_sad_best': float(inferred_sad_best),
            'minimap_shift_sad_0': float(inferred_sad_0),
            'candidates_count': int(len(sel_before.candidates)),
            'selected_marker_confidence': float(sel_before.confidence),
            'selected_marker_id': sel_before.selected_candidate_id,
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
        if is_prod_emergency() and str(ctx.config.mode).strip().lower() == 'real':
            _dump_marker_abort_pair(gate=str(gate), before=before, after=after, reason=str(out.abort_reason))
        elif dump_enabled():
            dump_pair(gate=str(gate), before=before, after=after, reason=str(out.abort_reason))
        _append_trace(
            gate=str(gate),
            payload={
                'event': 'abort',
                'tick_index': int(tick_index),
                'abort_reason': str(out.abort_reason),
                'candidates_count': int(len(eval_.sel_after_candidates)),
                'selected_marker_confidence': float(eval_.sel_after_confidence),
                'selected_marker_id': eval_.sel_after_candidate_id,
                'marker_candidates': eval_.sel_after_details.get('marker_candidates', []),
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

    if status in {'cavebot_marker_ambiguous', 'cavebot_marker_roi_black'}:
        out = CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason=str(status))
        if is_prod_emergency() and str(ctx.config.mode).strip().lower() == 'real':
            _dump_marker_abort_pair(gate=str(gate), before=before, after=after, reason=str(out.abort_reason))
        elif dump_enabled():
            dump_pair(gate=str(gate), before=before, after=after, reason=str(out.abort_reason))
        _append_trace(
            gate=str(gate),
            payload={
                'event': 'abort',
                'tick_index': int(tick_index),
                'abort_reason': str(out.abort_reason),
                'candidates_count': int(len(eval_.sel_after_candidates)),
                'selected_marker_confidence': float(eval_.sel_after_confidence),
                'selected_marker_id': eval_.sel_after_candidate_id,
                'marker_candidates': eval_.sel_after_details.get('marker_candidates', []),
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
        real_mode = str(getattr(ctx.config, 'mode', '')).strip().lower() == 'real'
        abort_streak_target = _wrong_direction_abort_streak(real_mode=bool(real_mode))
        try:
            current_streak = int(getattr(ctx.cavebot_gate.telemetry, 'wrong_direction_streak', 0)) + 1
        except Exception:
            current_streak = 1
        try:
            ctx.cavebot_gate.telemetry.wrong_direction_streak = int(current_streak)
        except Exception:
            pass
        if int(current_streak) < int(abort_streak_target):
            ctx.cavebot.gate_ticks_in_waypoint += 1
            ctx.cavebot.gate_attempts_used += 1
            return CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason=None)

        out = CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason='cavebot_wrong_direction')
        if dump_enabled():
            dump_pair(gate=str(gate), before=before, after=after, reason=str(out.abort_reason))
        _append_trace(
            gate=str(gate),
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

    try:
        ctx.cavebot_gate.telemetry.wrong_direction_streak = 0
    except Exception:
        pass

    if status == 'cavebot_no_progress':
        # No semantic progress: allow bounded retries, then deterministically abort stuck.
        # Stuck detection criteria over last N distances:
        # - no decreases at all (flat/increasing), OR
        # - oscillation (at least one decrease and at least one increase)
        real_mode = str(getattr(ctx.config, 'mode', '')).strip().lower() == 'real'
        N = _stuck_window(real_mode=bool(real_mode))
        distances = list(ctx.cavebot_gate.telemetry.last_n_distances)
        last_n = distances[-N:] if len(distances) >= N else []
        if len(last_n) >= N:
            eps = 1e-6
            dec = any((last_n[i] + eps) < last_n[i - 1] for i in range(1, N))
            inc = any((last_n[i] - eps) > last_n[i - 1] for i in range(1, N))
            if (not dec) or (dec and inc):
                out = CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason='cavebot_stuck_detected')
                if dump_enabled():
                    dump_pair(gate=str(gate), before=before, after=after, reason=str(out.abort_reason))
                _append_trace(
                    gate=str(gate),
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
