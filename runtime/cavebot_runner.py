from __future__ import annotations

import json
import logging
import os
import re
import time

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

from contracts.capture import CaptureAdapter, Frame
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import MinimapMarker, RuntimeContext, Waypoint
from contracts.window import WindowBindingAdapter
from diagnostics.last_frames import record_after, record_before
from diagnostics.frame_dump import dump_enabled, dump_pair
from runtime.cavebot_semantics import (
    ProgressResult,
    compute_progress,
    detect_player_marker,
    is_progress_valid,
    select_player_marker,
    MarkerSelection,
)
from runtime.minimap_localization import localize_minimap
from runtime.pacing import wait_until_ns
from runtime.profile import is_prod_emergency
from runtime.tibia_map_data import TibiaMapDataset, load_tibia_map_dataset
from runtime.event_correlation import attach_snapshot, new_event, validate
from runtime.trace_utils import serialize_for_trace
from runtime.cavebot_trace_helpers import make_waypoint_payload
from runtime.error_policy import should_reraise


_DATASET_CACHE: dict[str, TibiaMapDataset] = {}


def _absolute_waypoint_mode(waypoint: Waypoint) -> bool:
    raw = str(os.environ.get('FRBOT_CAVEBOT_WAYPOINT_SPACE', '') or '').strip().lower()
    if raw == 'world':
        return True
    opts = getattr(waypoint, 'options', {}) or {}
    if isinstance(opts, dict):
        if str(opts.get('coord_space', '') or '').strip().lower() == 'world':
            return True
        if 'world_x' in opts and 'world_y' in opts:
            return True
    return False


def _get_dataset_cached() -> TibiaMapDataset:
    key = str(os.environ.get('FRBOT_TIBIA_MAP_DATA_DIR', '') or '').strip()
    if key in _DATASET_CACHE:
        return _DATASET_CACHE[key]
    ds = load_tibia_map_dataset()
    _DATASET_CACHE[key] = ds
    return ds


def _effective_waypoint_for_frame(
    *,
    ctx: RuntimeContext,
    before: Frame,
    marker_before: MinimapMarker,
    waypoint: Waypoint,
) -> tuple[Waypoint, dict[str, object]]:
    if not _absolute_waypoint_mode(waypoint):
        return waypoint, {}

    opts = dict(getattr(waypoint, 'options', {}) or {})
    try:
        world_x = int(opts.get('world_x', waypoint.x))
        world_y = int(opts.get('world_y', waypoint.y))
        world_z = int(opts.get('world_z', waypoint.z))
    except Exception as exc:
        raise PreflightFailed('waypoints_localize_invalid_world_waypoint') from exc

    ds = _get_dataset_cached()
    if not bool(getattr(before, 'minimap_detected', False)):
        raise PreflightFailed('waypoints_localize_minimap_not_detected')

    prev_world = None
    try:
        tx = getattr(ctx.cavebot_gate.telemetry, 'world_x', None)
        ty = getattr(ctx.cavebot_gate.telemetry, 'world_y', None)
        if tx is not None and ty is not None:
            prev_world = (int(tx), int(ty))
    except Exception:
        if should_reraise():
            raise
        prev_world = None

    loc = localize_minimap(
        minimap_rgb=bytes(getattr(before, 'minimap_rgb', b'')),
        minimap_width=int(getattr(before, 'minimap_width', 0)),
        minimap_height=int(getattr(before, 'minimap_height', 0)),
        floor_z=int(world_z),
        dataset=ds,
        marker_px=(int(marker_before.x_px), int(marker_before.y_px)),
        marker_rgb=_marker_rgb(ctx),
        marker_tol=int(getattr(ctx.config, 'player_marker_tol', 30)),
        prev_player_world=prev_world,
    )

    min_score_raw = str(os.environ.get('FRBOT_CAVEBOT_LOCALIZE_MIN_SCORE', '0.55') or '0.55').strip()
    try:
        min_score = float(min_score_raw)
    except Exception:
        if should_reraise():
            raise
        min_score = 0.55
    min_score = max(0.0, min(1.0, float(min_score)))

    if bool(loc.ambiguous):
        err = PreflightFailed('waypoints_localize_ambiguous')
        setattr(err, 'details', {'score': float(loc.score), 'floor_z': int(world_z)})
        raise err
    if float(loc.score) < float(min_score):
        err = PreflightFailed('waypoints_localize_low_confidence')
        setattr(err, 'details', {'score': float(loc.score), 'min_score': float(min_score), 'floor_z': int(world_z)})
        raise err

    dx_world = int(world_x) - int(loc.player_x)
    dy_world = int(world_y) - int(loc.player_y)

    target_x = int(marker_before.x_px) + int(dx_world)
    target_y = int(marker_before.y_px) + int(dy_world)

    mm_w = int(getattr(before, 'minimap_width', 0))
    mm_h = int(getattr(before, 'minimap_height', 0))
    if mm_w > 0:
        target_x = max(0, min(int(mm_w - 1), int(target_x)))
    if mm_h > 0:
        target_y = max(0, min(int(mm_h - 1), int(target_y)))

    ctx.cavebot_gate.telemetry.world_x = int(loc.player_x)
    ctx.cavebot_gate.telemetry.world_y = int(loc.player_y)
    ctx.cavebot_gate.telemetry.world_z = int(loc.player_z)

    eff = Waypoint(
        waypoint_id=str(waypoint.waypoint_id),
        x=int(target_x),
        y=int(target_y),
        z=int(waypoint.z),
        radius_px=int(waypoint.radius_px),
        max_ticks=int(waypoint.max_ticks),
        waypoint_type=str(getattr(waypoint, 'waypoint_type', 'walk') or 'walk'),
        options=dict(getattr(waypoint, 'options', {}) or {}),
    )
    details = {
        'absolute_enabled': True,
        'world_waypoint': {'x': int(world_x), 'y': int(world_y), 'z': int(world_z)},
        'localized_player': {'x': int(loc.player_x), 'y': int(loc.player_y), 'z': int(loc.player_z)},
        'localized_score': float(loc.score),
        'resolved_target_px': {'x': int(target_x), 'y': int(target_y)},
    }
    return eff, details


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
        if should_reraise():
            raise
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
        if should_reraise():
            raise
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
        if should_reraise():
            raise
        return 1
    return max(1, min(int(val), 4))


_LOGGER = logging.getLogger(__name__)


def generate_run_id() -> str:
    """Generate unique run ID from timestamp + short random."""
    import datetime
    import uuid
    return f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


# Default RUN_ID generated at module load - can be overridden at runtime
_RUN_ID = generate_run_id()


def _stuck_window(*, real_mode: bool) -> int:
    raw = str(os.environ.get('FRBOT_CAVEBOT_STUCK_WINDOW') or '').strip()
    requested = None
    if raw:
        try:
            requested = int(raw)
        except Exception:
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass

    # Unit: number of distance samples (ticks) to analyze
    # Clamped: real_mode max=30, non-real max=15
    max_w = 30 if bool(real_mode) else 15
    if requested is None:
        applied = 5
        reason = "env_not_set_default"
    elif requested > max_w:
        applied = max_w
        reason = f"clamped_to_max_{max_w}"
    elif requested < 3:
        applied = 3
        reason = "clamped_to_min_3"
    else:
        applied = requested
        reason = "applied_as_configured"

    _LOGGER.info(
        "[%s] stuck_window: requested=%s, applied=%d, unit=ticks, max_allowed=%d, reason=%s",
        _RUN_ID, requested, applied, max_w, reason
    )
    return applied


def _select_key_toward_waypoint(marker_before: object, waypoint: Waypoint) -> str:
    mx = int(getattr(marker_before, 'x_px', 0))
    my = int(getattr(marker_before, 'y_px', 0))
    dx = int(waypoint.x) - int(mx)
    dy = int(waypoint.y) - int(my)

    # Prefer dominant axis deterministically.
    horiz = 'RIGHT' if dx > 0 else 'LEFT'
    vert = 'DOWN' if dy > 0 else 'UP'

    # Allow environment-gated inversion for horizontal axis when minimap
    # orientation or input mapping differs on some machines.
    try:
        raw_inv = str(os.environ.get('FRBOT_CAVEBOT_INVERT_HORIZONTAL') or '').strip().lower()
        invert_h = raw_inv in {'1', 'true', 'yes', 'on'}
    except Exception:
        invert_h = False

    if invert_h:
        horiz = 'LEFT' if horiz == 'RIGHT' else 'RIGHT'

    if abs(dx) >= abs(dy):
        return horiz
    return vert


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


def _append_trace(*, gate: str, payload: dict[str, Any]) -> None:
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
            f.write(json.dumps(serialize_for_trace(payload), sort_keys=True) + '\n')
    except Exception:
        return


def _emit_abort_return(*, ctx: RuntimeContext, input_: InputAdapter | None, gate: str, tick_index: int, abort_reason: str, blocked_reason: str, pnf: bool, inputs_sent: int, last_keys_sent: list[str], before_marker: Optional[MinimapMarker], after_marker: Optional[MinimapMarker], distance_before_px: float, distance_after_px: float, angle_deg: float, waypoint: Waypoint, extra: dict[str, Any] | None = None) -> CavebotTickOutcome:
    try:
        if is_prod_emergency() and str(ctx.config.mode).strip().lower() == 'real':
            try:
                _dump_marker_abort_pair(gate=str(gate), before=getattr(ctx, '_last_capture_before', None), after=getattr(ctx, '_last_capture_after', None), reason=str(abort_reason))
            except Exception:
                pass
        elif dump_enabled():
            try:
                dump_pair(gate=str(gate), before=getattr(ctx, '_last_capture_before', None), after=getattr(ctx, '_last_capture_after', None), reason=str(abort_reason))
            except Exception:
                pass
    except Exception:
        pass

    payload = {
        'event': 'abort',
        'tick_index': int(tick_index),
        'abort_reason': str(abort_reason),
        'blocked_reason': str(blocked_reason),
        'pnf': bool(pnf),
        'inputs_sent': int(inputs_sent),
        'last_keys_sent': list(last_keys_sent),
        'before_marker': _marker_payload(before_marker),
        'after_marker': _marker_payload(after_marker),
        'distance_before_px': float(distance_before_px),
        'distance_after_px': float(distance_after_px),
        'angle_deg': float(angle_deg),
        'waypoint': make_waypoint_payload(waypoint),
    }
    if extra:
        try:
            payload.update(dict(extra))
        except Exception:
            pass

    try:
        _append_trace(gate=str(gate), payload=payload)
    except Exception:
        pass

    try:
        if input_ is not None:
            _release_held_key(ctx, input_)
    except Exception:
        pass

    ev = CavebotTickEvidence(marker_before=before_marker, marker_after=after_marker, progress=None, status=str(abort_reason))
    return CavebotTickOutcome(reached_waypoint=False, evidence=ev, abort_reason=str(abort_reason))


def _emit_and_raise_abort(*, ctx: RuntimeContext, input_: InputAdapter | None, gate: str, tick_index: int, abort_reason: str, blocked_reason: str, pnf: bool, inputs_sent: int, last_keys_sent: list[str], before_marker: Optional[MinimapMarker], after_marker: Optional[MinimapMarker], distance_before_px: float, distance_after_px: float, angle_deg: float, waypoint: Waypoint, details: dict[str, Any] | None = None) -> None:
    out = _emit_abort_return(
        ctx=ctx,
        input_=input_,
        gate=gate,
        tick_index=tick_index,
        abort_reason=abort_reason,
        blocked_reason=blocked_reason,
        pnf=pnf,
        inputs_sent=inputs_sent,
        last_keys_sent=last_keys_sent,
        before_marker=before_marker,
        after_marker=after_marker,
        distance_before_px=distance_before_px,
        distance_after_px=distance_after_px,
        angle_deg=angle_deg,
        waypoint=waypoint,
        extra=(details or None),
    )

    abort_exc = PreflightFailed(str(abort_reason))
    try:
        if details:
            setattr(abort_exc, 'details', dict(details))
    except Exception:
        pass
    raise abort_exc


def _marker_payload(marker: Optional[MinimapMarker]) -> dict[str, object] | None:
    if marker is None:
        return None
    return {
        'x_px': int(getattr(marker, 'x_px', 0)),
        'y_px': int(getattr(marker, 'y_px', 0)),
        'pixel_count': int(getattr(marker, 'pixel_count', 0)),
    }


def _blocked_reason_from_abort(abort_reason: str | None) -> tuple[str, bool]:
    raw = str(abort_reason or '').strip().lower()
    if raw in {'cavebot_marker_not_found', 'cavebot_marker_ambiguous'}:
        return ('marker_not_found', False)
    if raw in {'cavebot_marker_roi_black', 'minimap_roi_black_or_static'}:
        return ('roi_invalid', False)
    if raw in {'cavebot_wrong_direction', 'cavebot_waypoint_timeout'}:
        return ('path_not_found', True)
    if raw in {'cavebot_needs_special_action'}:
        return ('needs_special_action', True)
    if raw in {'cavebot_stuck_detected', 'cavebot_no_progress'}:
        return ('move_key_no_effect', False)
    return ('none', False)


def _special_action_key_for_waypoint(waypoint: Waypoint) -> str:
    wp_type = str(getattr(waypoint, 'waypoint_type', 'walk') or 'walk').strip().lower()
    opts = getattr(waypoint, 'options', {}) or {}
    action_kind = str(opts.get('action_kind', '') or '').strip().lower() if isinstance(opts, dict) else ''

    if action_kind == 'rope' or wp_type == 'rope':
        return str(os.environ.get('FRBOT_ROPE_KEY', 'F8') or 'F8').strip() or 'F8'
    if action_kind in {'shovel', 'open_hole'}:
        return str(os.environ.get('FRBOT_SHOVEL_KEY', 'F9') or 'F9').strip() or 'F9'
    if action_kind == 'pick':
        return str(os.environ.get('FRBOT_PICK_KEY', 'F10') or 'F10').strip() or 'F10'
    if action_kind in {'ladder', 'stairs_up'} or wp_type in {'use_ladder', 'move_up'}:
        return str(os.environ.get('FRBOT_LADDER_UP_KEY', 'F11') or 'F11').strip() or 'F11'
    if action_kind == 'stairs_down' or wp_type == 'move_down':
        return str(os.environ.get('FRBOT_LADDER_DOWN_KEY', 'F12') or 'F12').strip() or 'F12'
    if wp_type == 'use_right_click' and action_kind in {'open_hole', 'shovel'}:
        return str(os.environ.get('FRBOT_SHOVEL_KEY', 'F9') or 'F9').strip() or 'F9'
    if wp_type == 'use_right_click' and action_kind == 'pick':
        return str(os.environ.get('FRBOT_PICK_KEY', 'F10') or 'F10').strip() or 'F10'
    return ''


def _is_special_action_waypoint(waypoint: Waypoint) -> bool:
    wp_type = str(getattr(waypoint, 'waypoint_type', 'walk') or 'walk').strip().lower()
    opts = getattr(waypoint, 'options', {}) or {}
    action_kind = str(opts.get('action_kind', '') or '').strip().lower() if isinstance(opts, dict) else ''
    return bool(
        wp_type in {'rope', 'use_ladder', 'use_right_click', 'move_up', 'move_down'}
        or action_kind in {'rope', 'shovel', 'pick', 'open_hole', 'ladder', 'stairs_up', 'stairs_down'}
    )


def _dialog_phrase_from_waypoint(waypoint: Waypoint) -> str:
    opts = getattr(waypoint, 'options', {}) or {}
    if not isinstance(opts, dict):
        return ''

    action_kind = str(opts.get('action_kind', '') or '').strip().lower()
    wp_type = str(getattr(waypoint, 'waypoint_type', '') or '').strip().lower()

    # New typed format: waypoint_type='call_npc', options={call, payload}
    if wp_type == 'call_npc' or action_kind == 'call_npc':
        call_name = str(opts.get('call', '') or '').strip().lower()
        payload = str(opts.get('payload', '') or '').strip()

        def _extract_sentence_new(raw: str) -> str:
            text = str(raw or '').strip()
            if not text:
                return ''
            m = re.search(r'"sentence"\s*:\s*"([^"]+)"', text, flags=re.IGNORECASE)
            if m is not None:
                return str(m.group(1) or '').strip().lower()
            return text.lower()

        if call_name == 'say':
            return _extract_sentence_new(payload)
        if call_name == 'talk_npc':
            return str(os.environ.get('FRBOT_CAVEBOT_NPC_GREETING', 'hi') or 'hi').strip().lower()
        return ''

    # Legacy format: action_kind='call', options={legacy_call, legacy_payload}
    if action_kind != 'call':
        return ''

    legacy_call = str(opts.get('legacy_call', '') or '').strip().lower()
    payload = str(opts.get('legacy_payload', '') or '').strip().lower()

    def _extract_sentence(raw: str) -> str:
        text = str(raw or '').strip()
        if not text:
            return ''
        m = re.search(r'"sentence"\s*:\s*"([^"]+)"', text, flags=re.IGNORECASE)
        if m is not None:
            return str(m.group(1) or '').strip().lower()
        return text.lower()

    if legacy_call == 'say':
        return _extract_sentence(payload)
    if legacy_call == 'talk_npc':
        return str(os.environ.get('FRBOT_CAVEBOT_NPC_GREETING', 'hi') or 'hi').strip().lower()
    return ''


def _chat_keys_for_phrase(phrase: str) -> list[str]:
    out: list[str] = []
    for ch in str(phrase or ''):
        if ch.isalnum():
            out.append(ch.lower())
        elif ch.isspace():
            out.append('SPACE')
    return out


def _run_dialog_waypoint(*, input_: InputAdapter, phrase: str) -> bool:
    keys = _chat_keys_for_phrase(str(phrase or ''))
    if not keys:
        return False
    input_.press_key('ENTER')
    for key in keys:
        input_.press_key(str(key))
    input_.press_key('ENTER')
    return True


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
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
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
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
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
                try:
                    from runtime.error_policy import should_reraise

                    if should_reraise():
                        raise
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


def _marker_reacquire_every() -> int:
    raw = str(os.environ.get('FRBOT_CAVEBOT_MARKER_REACQUIRE_EVERY') or '').strip()
    if not raw:
        return 6
    try:
        v = int(raw)
    except Exception:
        return 6
    return max(1, int(v))


def _marker_rgb(ctx: RuntimeContext) -> tuple[int, int, int]:
    try:
        tel = getattr(getattr(ctx, 'cavebot_gate', None), 'telemetry', None)
        rgb = getattr(tel, 'marker_rgb', None)
        if isinstance(rgb, (tuple, list)) and len(rgb) == 3:
            return (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    except Exception:
        if should_reraise():
            raise
    return _parse_rgb(ctx.config.player_marker_rgb)


def _release_held_key(ctx: RuntimeContext, input_: InputAdapter) -> None:
    """Release any held movement key. Safe to call unconditionally."""
    try:
        held = getattr(getattr(getattr(ctx, 'cavebot_gate', None), 'telemetry', None), 'held_key', None)
        if held:
            try:
                input_.key_up(str(held))
            except Exception:
                pass
            try:
                ctx.cavebot_gate.telemetry.held_key = None
            except Exception:
                pass
    except Exception:
        pass


def execute_cavebot_tick(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    waypoint: Waypoint,
    tick_index: int,
    gate: str = 'cavebot',
    tick_keys_override: list[str] | None = None,
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
    # Allow environment-controlled reacquire frequency to avoid expensive
    # marker re-selection on every tick. If a cached marker exists and the
    # current tick is not a reacquire tick, reuse previous marker.
    reacquire_every = int(_marker_reacquire_every())
    if prev_marker is not None and int(reacquire_every) > 1 and (int(tick_index) % int(reacquire_every)) != 0:
        sel_before = MarkerSelection(
            marker=prev_marker,
            candidates=(),
            selected_candidate_id=None,
            confidence=1.0,
            abort_reason=None,
            details={'cached': True},
        )
    else:
        sel_before = select_player_marker(
            before,
            marker_rgb=cfg_rgb,
            tol=int(ctx.config.player_marker_tol),
            min_pixels=int(ctx.config.player_marker_min_pixels),
            max_pixels=int(ctx.config.player_marker_max_pixels),
            prev_marker=prev_marker,
        )

    marker_before = sel_before.marker
    # Allow tests to inject a pre-populated `tick_keys_sent` to simulate contract violations.
    tick_keys_sent: list[str] = list(tick_keys_override) if tick_keys_override is not None else []

    # Marker aborts: must be explicit with evidence.
    if sel_before.abort_reason in {'cavebot_marker_ambiguous', 'cavebot_marker_roi_black'}:
        after = capture.grab()
        record_after(str(gate), after)
        blocked_reason, pnf = _blocked_reason_from_abort(str(sel_before.abort_reason))
        details = {
            'reason': str(sel_before.abort_reason),
            'blocked_reason': str(blocked_reason),
            'marker_candidates': sel_before.details.get('marker_candidates', []),
            'selected_marker': None,
            'selected_marker_id': sel_before.selected_candidate_id,
            'selected_marker_confidence': float(sel_before.confidence),
            'luma': {k: sel_before.details.get(k) for k in ['full_std_luma', 'roi_std_luma'] if k in sel_before.details},
            'roi_sanity_reason': ('minimap_roi_black_or_static' if str(sel_before.abort_reason) == 'cavebot_marker_roi_black' else ''),
        }
        _emit_and_raise_abort(
            ctx=ctx,
            input_=input_,
            gate=str(gate),
            tick_index=int(tick_index),
            abort_reason=str(sel_before.abort_reason),
            blocked_reason=str(blocked_reason),
            pnf=bool(pnf),
            inputs_sent=0,
            last_keys_sent=[],
            before_marker=None,
            after_marker=None,
            distance_before_px=0.0,
            distance_after_px=0.0,
            angle_deg=0.0,
            waypoint=waypoint,
            details=details,
        )

    if marker_before is None:
        after = capture.grab()
        record_after(str(gate), after)
        blocked_reason, pnf = _blocked_reason_from_abort('cavebot_marker_not_found')
        return _emit_abort_return(
            ctx=ctx,
            input_=input_,
            gate=str(gate),
            tick_index=int(tick_index),
            abort_reason='cavebot_marker_not_found',
            blocked_reason=str(blocked_reason),
            pnf=bool(pnf),
            inputs_sent=0,
            last_keys_sent=[],
            before_marker=None,
            after_marker=None,
            distance_before_px=0.0,
            distance_after_px=0.0,
            angle_deg=0.0,
            waypoint=waypoint,
            extra={'candidates_count': int(len(sel_before.candidates)), 'selected_marker_confidence': float(sel_before.confidence), 'selected_marker_id': sel_before.selected_candidate_id, 'marker_candidates': sel_before.details.get('marker_candidates', [])},
        )

    # Persist marker telemetry for next tick stabilization.
    ctx.cavebot_gate.telemetry.marker_before = marker_before

    waypoint_eff = waypoint
    localization_details: dict[str, object] = {}
    try:
        waypoint_eff, localization_details = _effective_waypoint_for_frame(
            ctx=ctx,
            before=before,
            marker_before=marker_before,
            waypoint=waypoint,
        )
    except PreflightFailed:
        raise
    except Exception as exc:
        raise PreflightFailed('waypoints_localize_failed') from exc

    # Non-movement dialog waypoint: execute chat phrase and complete immediately.
    dialog_phrase = _dialog_phrase_from_waypoint(waypoint)
    if dialog_phrase:
        try:
            binding.assert_bound()
        except Exception as exc:
            raise PreflightFailed('cavebot_window_binding_lost') from exc

        input_ts_ns = int(time.monotonic_ns())
        attach_snapshot(event, stage='input', ts_ns=input_ts_ns, status=binding.snapshot())

        sent = _run_dialog_waypoint(input_=input_, phrase=dialog_phrase)
        if sent:
            ctx.cavebot.gate_inputs_sent += 1
            tick_keys_sent.append('CHAT')

        try:
            raw = os.environ.get('FRBOT_POST_CHAT_DELAY_MS', '')
            if str(raw).strip() == '':
                post_chat_ms = 120 if str(ctx.config.mode).strip().lower() == 'real' else 0
            else:
                post_chat_ms = int(raw)
        except Exception:
            post_chat_ms = 120 if str(ctx.config.mode).strip().lower() == 'real' else 0
        if int(post_chat_ms) > 0:
            wait_until_ns(int(time.monotonic_ns() + (int(post_chat_ms) * 1_000_000)))

        after = capture.grab()
        after_ts_ns = int(time.monotonic_ns())
        attach_snapshot(event, stage='after', ts_ns=after_ts_ns, status=binding.snapshot())
        record_after(str(gate), after)

        marker_after = detect_player_marker(
            after,
            marker_rgb=_marker_rgb(ctx),
            tol=int(ctx.config.player_marker_tol),
            min_pixels=int(ctx.config.player_marker_min_pixels),
            max_pixels=int(ctx.config.player_marker_max_pixels),
        )
        evidence = CavebotTickEvidence(marker_before=marker_before, marker_after=marker_after, progress=None, status='ok')
        ctx.cavebot.gate_ticks_in_waypoint += 1

        _append_trace(
            gate=str(gate),
            payload={
                'event': 'WAYPOINT_ACTION',
                'tick_index': int(tick_index),
                'input_sent': bool(sent),
                'key': 'CHAT',
                'last_keys_sent': list(tick_keys_sent),
                'inputs_sent': (1 if sent else 0),
                'before_marker': _marker_payload(marker_before),
                'after_marker': _marker_payload(marker_after),
                'distance_before_px': float(_waypoint_distance_px(marker_before, waypoint_eff)),
                'distance_after_px': float(_waypoint_distance_px(marker_after, waypoint_eff)),
                'angle_deg': 0.0,
                'blocked_reason': 'none',
                'pnf': False,
                'phrase': str(dialog_phrase),
                'abort_reason': 'none',
                'waypoint': make_waypoint_payload(waypoint),
            },
        )
        return CavebotTickOutcome(reached_waypoint=True, evidence=evidence, abort_reason=None, event='WAYPOINT_ACTION')

    if _is_special_action_waypoint(waypoint):
        special_key = _special_action_key_for_waypoint(waypoint)
        if not special_key:
            ev = CavebotTickEvidence(marker_before=marker_before, marker_after=None, progress=None, status='cavebot_needs_special_action')
            out = CavebotTickOutcome(reached_waypoint=False, evidence=ev, abort_reason='cavebot_needs_special_action')
            blocked_reason, pnf = _blocked_reason_from_abort(out.abort_reason)
            _append_trace(
                gate=str(gate),
                payload={
                    'event': 'abort',
                    'tick_index': int(tick_index),
                    'abort_reason': str(out.abort_reason),
                    'blocked_reason': str(blocked_reason),
                    'pnf': bool(pnf),
                    'inputs_sent': 0,
                    'last_keys_sent': [],
                    'before_marker': _marker_payload(marker_before),
                    'after_marker': _marker_payload(None),
                    'distance_before_px': float(_waypoint_distance_px(marker_before, waypoint_eff)),
                    'distance_after_px': float(_waypoint_distance_px(None, waypoint_eff)),
                    'angle_deg': 0.0,
                    'waypoint': make_waypoint_payload(waypoint),
                },
            )
            return out

        try:
            binding.assert_bound()
        except Exception as exc:
            raise PreflightFailed('cavebot_window_binding_lost') from exc

        input_ts_ns = int(time.monotonic_ns())
        attach_snapshot(event, stage='input', ts_ns=input_ts_ns, status=binding.snapshot())

        input_.press_key(str(special_key))
        tick_keys_sent.append(str(special_key))
        ctx.cavebot.gate_inputs_sent += 1

        if len(tick_keys_sent) != 1:
            raise PreflightFailed('cavebot_input_contract_violation')

        try:
            raw = os.environ.get('FRBOT_POST_ACTION_DELAY_MS', '')
            if str(raw).strip() == '':
                post_action_ms = 200 if str(ctx.config.mode).strip().lower() == 'real' else 0
            else:
                post_action_ms = int(raw)
        except Exception:
            post_action_ms = 200 if str(ctx.config.mode).strip().lower() == 'real' else 0
        if int(post_action_ms) > 0:
            wait_until_ns(int(time.monotonic_ns() + (int(post_action_ms) * 1_000_000)))

        after = capture.grab()
        after_ts_ns = int(time.monotonic_ns())
        attach_snapshot(event, stage='after', ts_ns=after_ts_ns, status=binding.snapshot())
        record_after(str(gate), after)

        marker_after = detect_player_marker(
            after,
            marker_rgb=_marker_rgb(ctx),
            tol=int(ctx.config.player_marker_tol),
            min_pixels=int(ctx.config.player_marker_min_pixels),
            max_pixels=int(ctx.config.player_marker_max_pixels),
        )
        dist_before = float(_waypoint_distance_px(marker_before, waypoint_eff))
        dist_after = float(_waypoint_distance_px(marker_after, waypoint_eff))
        moved = False
        if marker_after is not None:
            moved = (
                int(getattr(marker_after, 'x_px', 0)) != int(getattr(marker_before, 'x_px', 0))
                or int(getattr(marker_after, 'y_px', 0)) != int(getattr(marker_before, 'y_px', 0))
                or float(dist_after) < float(dist_before)
            )

        if not moved:
            blocked_reason, pnf = _blocked_reason_from_abort('cavebot_needs_special_action')
            return _emit_abort_return(
                ctx=ctx,
                input_=input_,
                gate=str(gate),
                tick_index=int(tick_index),
                abort_reason='cavebot_needs_special_action',
                blocked_reason=str(blocked_reason),
                pnf=bool(pnf),
                inputs_sent=1,
                last_keys_sent=list(tick_keys_sent),
                before_marker=marker_before,
                after_marker=marker_after,
                distance_before_px=float(dist_before),
                distance_after_px=float(dist_after),
                angle_deg=0.0,
                waypoint=waypoint,
                extra=None,
            )

        evidence = CavebotTickEvidence(marker_before=marker_before, marker_after=marker_after, progress=None, status='ok')
        ctx.cavebot.gate_ticks_in_waypoint += 1
        _append_trace(
            gate=str(gate),
            payload={
                'event': 'WAYPOINT_ACTION',
                'tick_index': int(tick_index),
                'input_sent': True,
                'key': str(special_key),
                'last_keys_sent': list(tick_keys_sent),
                'inputs_sent': 1,
                'before_marker': _marker_payload(marker_before),
                'after_marker': _marker_payload(marker_after),
                'distance_before_px': float(dist_before),
                'distance_after_px': float(dist_after),
                'angle_deg': 0.0,
                'blocked_reason': 'none',
                'pnf': False,
                'abort_reason': 'none',
                'waypoint': make_waypoint_payload(waypoint),
            },
        )
        _release_held_key(ctx, input_)
        return CavebotTickOutcome(reached_waypoint=True, evidence=evidence, abort_reason=None, event='WAYPOINT_ACTION')

    # Waypoint timeout is deterministic (no further input).
    if int(ctx.cavebot.gate_ticks_in_waypoint) >= int(waypoint.max_ticks):
        after = capture.grab()
        record_after(str(gate), after)
        blocked_reason, pnf = _blocked_reason_from_abort('cavebot_waypoint_timeout')
        return _emit_abort_return(
            ctx=ctx,
            input_=input_,
            gate=str(gate),
            tick_index=int(tick_index),
            abort_reason='cavebot_waypoint_timeout',
            blocked_reason=str(blocked_reason),
            pnf=bool(pnf),
            inputs_sent=0,
            last_keys_sent=[],
            before_marker=marker_before,
            after_marker=None,
            distance_before_px=float(_waypoint_distance_px(marker_before, waypoint_eff)),
            distance_after_px=float(_waypoint_distance_px(None, waypoint_eff)),
            angle_deg=0.0,
            waypoint=waypoint,
            extra=None,
        )

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

    dist_before = _waypoint_distance_px(virt_before, waypoint_eff)
    if float(dist_before) <= float(waypoint_eff.radius_px):
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

        reached_now = int(ctx.cavebot.gate_reach_streak) >= 2 and float(dist_after) <= float(waypoint_eff.radius_px)
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
                'last_keys_sent': [],
                'inputs_sent': 0,
                'before_marker': _marker_payload(marker_before),
                'after_marker': _marker_payload(marker_after),
                'reach_streak': int(ctx.cavebot.gate_reach_streak),
                'distance_before_px': float(dist_before),
                'distance_after_px': float(dist_after),
                'angle_deg': 0.0,
                'abort_reason': 'none',
                'blocked_reason': 'none',
                'pnf': False,
                'waypoint': make_waypoint_payload(waypoint),
                'waypoint_effective': {
                    'x': int(waypoint_eff.x),
                    'y': int(waypoint_eff.y),
                    'z': int(waypoint_eff.z),
                    'radius_px': int(waypoint_eff.radius_px),
                },
                'localization': dict(localization_details),
            },
        )
        _release_held_key(ctx, input_)
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
    key = _select_key_toward_waypoint(key_marker, waypoint_eff)

    # Enforce strong binding immediately before input.
    try:
        binding.assert_bound()
    except Exception as exc:
        raise PreflightFailed('cavebot_window_binding_lost') from exc

    input_ts_ns = int(time.monotonic_ns())
    attach_snapshot(event, stage='input', ts_ns=input_ts_ns, status=binding.snapshot())

    # Hold-key: always hold direction key for smooth continuous walking.
    # First tick or direction change: release old + press-hold new.
    # Same direction: skip physical input (key already held, Tibia auto-walks).
    _held_key = getattr(getattr(getattr(ctx, 'cavebot_gate', None), 'telemetry', None), 'held_key', None)
    if str(_held_key or '') != str(key):
        # Direction changed or first tick: release old key, press-hold new key.
        if _held_key:
            try:
                input_.key_up(str(_held_key))
            except Exception:
                pass
        try:
            input_.key_down(str(key))
        except Exception:
            input_.press_key(str(key))
        try:
            ctx.cavebot_gate.telemetry.held_key = str(key)
        except Exception:
            pass
    else:
        # Same direction already held — no new OS input needed (Tibia auto-walks).
        # Notify mock adapters so they can simulate one step of movement.
        try:
            input_.auto_walk_tick(str(key))
        except Exception:
            pass
    tick_keys_sent.append(str(key))
    ctx.cavebot.gate_inputs_sent += 1

    if len(tick_keys_sent) != 1:
        raise PreflightFailed('cavebot_input_contract_violation')

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
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
        raise corr_exc

    eval_ = _progress_from_frames(ctx, before, after, waypoint_eff)
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
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass

    _append_trace(
        gate=str(gate),
        payload={
            'event': 'tick',
            'tick_index': int(tick_index),
            'input_sent': True,
            'key': str(key),
            'last_keys_sent': list(tick_keys_sent),
            'inputs_sent': 1,
            'before_marker': _marker_payload(marker_before),
            'after_marker': _marker_payload(marker_after),
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
            'blocked_reason': 'none',
            'pnf': False,
            'waypoint': make_waypoint_payload(waypoint),
            'waypoint_effective': {
                'x': int(waypoint_eff.x),
                'y': int(waypoint_eff.y),
                'z': int(waypoint_eff.z),
                'radius_px': int(waypoint_eff.radius_px),
            },
            'localization': dict(localization_details),
        },
    )

    if status == 'cavebot_marker_not_found':
        blocked_reason, pnf = _blocked_reason_from_abort('cavebot_marker_not_found')
        return _emit_abort_return(
            ctx=ctx,
            input_=input_,
            gate=str(gate),
            tick_index=int(tick_index),
            abort_reason='cavebot_marker_not_found',
            blocked_reason=str(blocked_reason),
            pnf=bool(pnf),
            inputs_sent=1,
            last_keys_sent=list(tick_keys_sent),
            before_marker=marker_before,
            after_marker=marker_after,
            distance_before_px=float(dist_b),
            distance_after_px=float(dist_a),
            angle_deg=float(progress.angle_deg) if progress is not None else 0.0,
            waypoint=waypoint,
            extra={'candidates_count': int(len(eval_.sel_after_candidates)), 'selected_marker_confidence': float(eval_.sel_after_confidence), 'selected_marker_id': eval_.sel_after_candidate_id, 'marker_candidates': eval_.sel_after_details.get('marker_candidates', [])},
        )

    if status in {'cavebot_marker_ambiguous', 'cavebot_marker_roi_black'}:
        blocked_reason, pnf = _blocked_reason_from_abort(str(status))
        extra = {'candidates_count': int(len(eval_.sel_after_candidates)), 'selected_marker_confidence': float(eval_.sel_after_confidence), 'selected_marker_id': eval_.sel_after_candidate_id, 'marker_candidates': eval_.sel_after_details.get('marker_candidates', [])}
        if str(status) == 'cavebot_marker_roi_black':
            extra['roi_sanity_reason'] = 'minimap_roi_black_or_static'
        return _emit_abort_return(
            ctx=ctx,
            input_=input_,
            gate=str(gate),
            tick_index=int(tick_index),
            abort_reason=str(status),
            blocked_reason=str(blocked_reason),
            pnf=bool(pnf),
            inputs_sent=1,
            last_keys_sent=list(tick_keys_sent),
            before_marker=marker_before,
            after_marker=marker_after,
            distance_before_px=float(dist_b),
            distance_after_px=float(dist_a),
            angle_deg=float(progress.angle_deg) if progress is not None else 0.0,
            waypoint=waypoint,
            extra=extra,
        )

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
            try:
                from runtime.error_policy import should_reraise

                if should_reraise():
                    raise
            except Exception:
                pass
        if int(current_streak) < int(abort_streak_target):
            ctx.cavebot.gate_ticks_in_waypoint += 1
            ctx.cavebot.gate_attempts_used += 1
            return CavebotTickOutcome(reached_waypoint=False, evidence=evidence, abort_reason=None)

        blocked_reason, pnf = _blocked_reason_from_abort('cavebot_wrong_direction')
        return _emit_abort_return(
            ctx=ctx,
            input_=input_,
            gate=str(gate),
            tick_index=int(tick_index),
            abort_reason='cavebot_wrong_direction',
            blocked_reason=str(blocked_reason),
            pnf=bool(pnf),
            inputs_sent=1,
            last_keys_sent=list(tick_keys_sent),
            before_marker=marker_before,
            after_marker=marker_after,
            distance_before_px=float(dist_b),
            distance_after_px=float(dist_a),
            angle_deg=float(progress.angle_deg) if progress is not None else 0.0,
            waypoint=waypoint,
            extra=None,
        )

    try:
        ctx.cavebot_gate.telemetry.wrong_direction_streak = 0
    except Exception:
        if should_reraise():
            raise

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
                blocked_reason, pnf = _blocked_reason_from_abort('cavebot_stuck_detected')
                return _emit_abort_return(
                    ctx=ctx,
                    input_=input_,
                    gate=str(gate),
                    tick_index=int(tick_index),
                    abort_reason='cavebot_stuck_detected',
                    blocked_reason=str(blocked_reason),
                    pnf=bool(pnf),
                    inputs_sent=1,
                    last_keys_sent=list(tick_keys_sent),
                    before_marker=marker_before,
                    after_marker=marker_after,
                    distance_before_px=float(dist_b),
                    distance_after_px=float(dist_a),
                    angle_deg=float(progress.angle_deg) if progress is not None else 0.0,
                    waypoint=waypoint,
                    extra=None,
                )

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
