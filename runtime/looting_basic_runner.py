from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import json
import os
import time

from contracts.capture import CaptureAdapter, Frame
from contracts.evidence import Roi
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import InventorySnapshot, RuntimeContext
from contracts.window import WindowBindingAdapter
from diagnostics.last_frames import record_after, record_before
from runtime.chat_loot_semantics import LootEvidence, detect_loot_from_chat
from runtime.inventory_semantics import (
    InventoryDelta,
    beef_candidate_u16,
    diff_inventory,
    is_loot_success,
    rank_beef_candidates_by_temporal_stability_fast,
    read_inventory_pair_binary,
    scan_beef_candidates_in_frame,
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _frames_dir_for_looting_evidence() -> str:
    raw = (os.environ.get('FRBOT_REAL_FRAMES_DIR', '') or '').strip()
    if raw:
        return raw
    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    if profile == 'prod_emergency':
        return str(Path('diagnostics') / 'frames_emergency')
    if profile == 'prod_full':
        return str(Path('diagnostics') / 'frames_full')
    return str(Path('diagnostics') / 'frames')


def _try_dump_looting_pair(
    *,
    gate: str = 'looting_basic',
    reason: str,
    before: Frame | None,
    after: Frame | None,
) -> tuple[str | None, str | None]:
    try:
        from diagnostics.frame_dump import dump_enabled, dump_pair

        profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        if profile not in {'prod_emergency', 'prod_full'} and not dump_enabled():
            return (None, None)

        g = (gate or 'looting_basic').strip().lower() or 'looting_basic'
        return dump_pair(
            gate=str(g),
            before=before,
            after=after,
            reason=str(reason),
            out_dir=_frames_dir_for_looting_evidence(),
        )
    except Exception:
        return (None, None)


def _try_write_looting_basic_last_result(
    *,
    gate: str = 'looting_basic',
    evidence_dir: str,
    ok: bool,
    outcome_kind: str,
    loot_action: dict | None,
    verify_attempts: int,
    inventory_after_readable: bool,
    inventory_delta_ok: bool,
    inventory_before: InventorySnapshot | None = None,
    inventory_after: InventorySnapshot | None = None,
    chat_evidence: LootEvidence | None,
    used_chat_fallback: bool = False,
    before_ppm: str | None = None,
    after_ppm: str | None = None,
) -> None:
    """Write a small metadata file for tooling (audit/cert scripts).

    This is not an authority by itself; it is a pointer to the last dumped evidence.
    """

    try:
        out_dir = Path(str(evidence_dir))
        out_dir.mkdir(parents=True, exist_ok=True)

        g = (gate or 'looting_basic').strip().lower() or 'looting_basic'

        if not before_ppm or not after_ppm:
            before_scan = None
            after_scan = None
            try:
                items = sorted(out_dir.glob(f'{g}_*_before.ppm'))
                if items:
                    before_scan = str(items[-1].name)
                    after_candidate = str(items[-1].name).replace('_before.ppm', '_after.ppm')
                    if (out_dir / after_candidate).exists():
                        after_scan = str(after_candidate)
            except Exception:
                before_scan = None
                after_scan = None

            before_ppm = before_ppm or before_scan
            after_ppm = after_ppm or after_scan

        delta_latency_ms: float | None = None
        if chat_evidence is not None:
            try:
                v = (chat_evidence.debug or {}).get('delta_latency_ms')
                if v is not None:
                    delta_latency_ms = float(v)
            except Exception:
                delta_latency_ms = None

        payload: dict[str, object] = {
            'gate': str(g),
            'ok': bool(ok),
            'outcome_kind': str(outcome_kind),
            'loot_action': dict(loot_action or {}),
            'verify_attempts': int(verify_attempts),
            'inventory_after_readable': bool(inventory_after_readable),
            'inventory_delta_ok': bool(inventory_delta_ok),
            'inventory_before': None
            if inventory_before is None
            else {
                'slot_counts': dict(inventory_before.slot_counts or {}),
                'capacity_used': inventory_before.capacity_used,
            },
            'inventory_after': None
            if inventory_after is None
            else {
                'slot_counts': dict(inventory_after.slot_counts or {}),
                'capacity_used': inventory_after.capacity_used,
            },
            'chat_ok': bool(chat_evidence.ok) if chat_evidence is not None else False,
            'chat_debug': dict(chat_evidence.debug or {}) if chat_evidence is not None else {},
            'delta_latency_ms': delta_latency_ms,
            'used_chat_fallback': bool(used_chat_fallback),
            'before_ppm': before_ppm,
            'after_ppm': after_ppm,
        }

        (out_dir / f'{g}_last_result.json').write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    except Exception:
        return


def _try_dump_click_overlay(*, reason: str, frame: Frame | None, x: int | None, y: int | None) -> None:
    if frame is None or x is None or y is None:
        return
    try:
        from diagnostics.frame_dump import dump_enabled
        from diagnostics.overlay_dump import dump_click_point_overlay

        if not dump_enabled():
            return
        dump_click_point_overlay(
            frames_dir=Path(_frames_dir_for_looting_evidence()),
            frame=frame,
            x=int(x),
            y=int(y),
            reason=str(reason),
        )
    except Exception:
        return


def _roi_center(roi: Roi) -> tuple[int, int]:
    cx = int(roi.x) + (int(roi.width) // 2)
    cy = int(roi.y) + (int(roi.height) // 2)
    return cx, cy


def _safe_int(v: Any | None) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except Exception:
        return None


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    s = str(raw).strip()
    if s == '':
        return None
    try:
        return int(s)
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class LootingBasicOutcome:
    ok: bool
    evidence_kind: str
    inventory_before: Optional[InventorySnapshot]
    inventory_after: Optional[InventorySnapshot]
    delta: Optional[InventoryDelta]


def execute_looting_basic_once(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    gate: str = 'looting_basic',
) -> LootingBasicOutcome:
    """Execute exactly one looting_basic action.

    BEFORE capture -> 1 input -> AFTER capture -> semantic evidence validation.
    Any ambiguity -> abort.
    """

    try:
        binding.assert_bound()
    except Exception as exc:
        raise PreflightFailed('looting_window_binding_lost') from exc

    inv_roi = ctx.rois.get(ctx.config.inventory_text_roi)
    if inv_roi is None:
        raise PreflightFailed('looting_inventory_unreadable')

    chat_roi_name = (os.environ.get('FRBOT_CHAT_LOOT_ROI', '') or 'chat_loot_area').strip() or 'chat_loot_area'
    chat_roi = ctx.rois.get(chat_roi_name)

    gate_name = (gate or 'looting_basic').strip().lower() or 'looting_basic'

    before = capture.grab()
    record_before(gate_name, before)
    record_before('runtime', before)

    def _corpse_present_in_roi(*, frame: Frame, roi: Roi) -> bool:
        try:
            rgb = bytes(getattr(frame, 'rgb', b'') or b'')
            w = int(getattr(frame, 'width', 0) or 0)
            h = int(getattr(frame, 'height', 0) or 0)
            if not rgb or w <= 0 or h <= 0:
                return False
            if len(rgb) != (w * h * 3):
                return False

            x0 = int(getattr(roi, 'x', 0) or 0)
            y0 = int(getattr(roi, 'y', 0) or 0)
            rw = int(getattr(roi, 'width', 0) or 0)
            rh = int(getattr(roi, 'height', 0) or 0)
            if x0 < 0 or y0 < 0 or rw <= 0 or rh <= 0:
                return False
            if (x0 + rw) > w or (y0 + rh) > h:
                return False

            # Heuristic-only: local contrast + weak brownish ratio.
            step = 2
            row_stride = w * 3
            tol_uniform = 4.0
            tol_edge = 10.0
            corpse_ratio_min = 0.06

            lumas: list[int] = []
            edge_sum = 0.0
            edge_count = 0
            corpse_like = 0
            total = 0

            prev_row_luma: list[int] | None = None
            for yy in range(y0, y0 + rh, step):
                row_luma: list[int] = []
                base = yy * row_stride
                prev_l = None
                for xx in range(x0, x0 + rw, step):
                    i = base + (xx * 3)
                    if i + 2 >= len(rgb):
                        continue
                    r = int(rgb[i + 0])
                    g = int(rgb[i + 1])
                    b = int(rgb[i + 2])
                    l = (r * 30 + g * 59 + b * 11) // 100
                    lumas.append(l)
                    row_luma.append(l)

                    mid = (l >= 25) and (l <= 210)
                    brownish = (r >= g) and (g >= b) and ((r - b) >= 12)
                    if mid and brownish:
                        corpse_like += 1
                    total += 1

                    if prev_l is not None:
                        edge_sum += abs(l - prev_l)
                        edge_count += 1
                    prev_l = l

                if prev_row_luma is not None:
                    m = min(len(prev_row_luma), len(row_luma))
                    for k in range(m):
                        edge_sum += abs(int(row_luma[k]) - int(prev_row_luma[k]))
                        edge_count += 1
                prev_row_luma = row_luma

            if not lumas or total <= 0:
                return False

            mean = sum(lumas) / float(len(lumas))
            var = sum((v - mean) * (v - mean) for v in lumas) / float(len(lumas))
            std = var ** 0.5
            edge = (edge_sum / float(edge_count)) if edge_count > 0 else 0.0
            corpse_ratio = float(corpse_like) / float(total)

            if std < tol_uniform and edge < tol_edge:
                return False
            return corpse_ratio >= corpse_ratio_min
        except Exception:
            return False

    def _try_dump_emergency_candidates(*, reason: str, before_frame: Frame, after_frames: list[Frame]) -> list[dict[str, object]]:
        profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        if profile != 'prod_emergency':
            return []
        try:
            out_dir = Path(_frames_dir_for_looting_evidence())
            out_dir.mkdir(parents=True, exist_ok=True)

            try:
                cap_max = int((os.environ.get('FRBOT_INVENTORY_BINARY_CAP_MAX', '50000') or '50000').strip() or '50000')
            except Exception:
                cap_max = 50000
            cap_max = max(1, min(int(cap_max), 65535))

            before_hits = scan_beef_candidates_in_frame(before_frame, limit=200, cap_max=int(cap_max), gold_max=None)
            after_last = after_frames[-1] if after_frames else before_frame
            after_hits = scan_beef_candidates_in_frame(after_last, limit=200, cap_max=int(cap_max), gold_max=None)

            stable = rank_beef_candidates_by_temporal_stability_fast(
                before=before_frame,
                after_frames=after_frames,
                cap_max=int(cap_max),
                gold_max=None,
                top_n=50,
                scan_limit=200,
            )

            (out_dir / 'emergency_inventory_binary_beef_candidates.json').write_text(
                (
                    json.dumps(
                        {
                            'gate': 'looting_basic',
                            'reason': str(reason),
                            'cap_max': int(cap_max),
                            'before': {
                                'frame_name': 'looting_basic_before',
                                'count': int(len(before_hits)),
                                'candidates': [
                                    {
                                        'x': int(c.x),
                                        'y': int(c.y),
                                        'w': 2,
                                        'h': 1,
                                        'raw6_hex': str(c.raw6_hex),
                                        'u16': beef_candidate_u16(str(c.raw6_hex)),
                                    }
                                    for c in before_hits
                                ],
                            },
                            'after_last': {
                                'frame_name': 'looting_basic_after_last',
                                'count': int(len(after_hits)),
                                'candidates': [
                                    {
                                        'x': int(c.x),
                                        'y': int(c.y),
                                        'w': 2,
                                        'h': 1,
                                        'raw6_hex': str(c.raw6_hex),
                                        'u16': beef_candidate_u16(str(c.raw6_hex)),
                                    }
                                    for c in after_hits
                                ],
                            },
                            'stable_top': stable,
                            'stable_method': 'fast_limited_scan',
                            'after_frames_count': int(len(after_frames)),
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + '\n'
                ),
                encoding='utf-8',
            )
            return stable
        except Exception:
            return []

    def _context_menu_like_opened(*, before_frame: Frame, after_frame: Frame, x: int, y: int) -> bool:
        """Best-effort heuristic: detect right-click context menu vs quick-loot.

        No OCR. Uses a localized pixel-diff around the click point.
        Only intended to upgrade failure reason when inventory delta is 0.
        """

        try:
            rgb0 = bytes(getattr(before_frame, 'rgb', b'') or b'')
            rgb1 = bytes(getattr(after_frame, 'rgb', b'') or b'')
            w = int(getattr(before_frame, 'width', 0) or 0)
            h = int(getattr(before_frame, 'height', 0) or 0)
            if not rgb0 or not rgb1 or w <= 0 or h <= 0:
                return False
            if len(rgb0) != len(rgb1):
                return False

            step = 4
            half = 140
            x0 = max(0, min(int(w - 1), int(x)))
            y0 = max(0, min(int(h - 1), int(y)))
            left = max(0, x0 - half)
            right = min(int(w - 1), x0 + half)
            top = max(0, y0 - half)
            bottom = min(int(h - 1), y0 + half)
            if right <= left or bottom <= top:
                return False

            row_stride = int(w) * 3

            changed = 0
            total = 0
            minx = 10**9
            miny = 10**9
            maxx = -1
            maxy = -1

            tol = 18
            for yy in range(int(top), int(bottom) + 1, int(step)):
                base = int(yy) * row_stride
                for xx in range(int(left), int(right) + 1, int(step)):
                    i = base + (int(xx) * 3)
                    if i + 2 >= len(rgb0):
                        continue
                    dr = abs(int(rgb0[i + 0]) - int(rgb1[i + 0]))
                    dg = abs(int(rgb0[i + 1]) - int(rgb1[i + 1]))
                    db = abs(int(rgb0[i + 2]) - int(rgb1[i + 2]))
                    total += 1
                    if dr > tol or dg > tol or db > tol:
                        changed += 1
                        minx = min(int(minx), int(xx))
                        miny = min(int(miny), int(yy))
                        maxx = max(int(maxx), int(xx))
                        maxy = max(int(maxy), int(yy))

            if total <= 0 or changed <= 0:
                return False

            changed_pct = float(changed) / float(total)
            if changed_pct < 0.06:
                return False

            if maxx < minx or maxy < miny:
                return False

            bw = int(maxx - minx)
            bh = int(maxy - miny)

            # Context menus are medium rectangles near the click.
            if bw < 80 or bh < 80:
                return False
            if bw > 520 or bh > 520:
                return False

            # Avoid treating full-region redraw/animation as menu.
            region_w = int(right - left)
            region_h = int(bottom - top)
            if bw > int(region_w * 0.90) and bh > int(region_h * 0.90):
                return False

            return True
        except Exception:
            return False

    # Read BEFORE inventory (pair read happens after AFTER grab).
    inv_pair_before = read_inventory_pair_binary(before, before, inv_roi)
    if inv_pair_before is None:
        _try_dump_looting_pair(gate=gate_name, reason='looting_inventory_unreadable', before=before, after=None)
        _try_dump_emergency_candidates(reason='looting_inventory_unreadable', before_frame=before, after_frames=[])
        raise PreflightFailed('looting_inventory_unreadable')
    inv_before, _ = inv_pair_before
    ctx.looting.last_inventory = inv_before

    # Certification precondition: attempt only if a corpse is detectable.
    profile0 = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    if profile0 == 'prod_emergency' and str(getattr(ctx.config, 'mode', '') or '').strip().lower() == 'real':
        corpse_roi_name = (os.environ.get('FRBOT_LOOT_CORPSE_ROI', '') or 'loot_corpse').strip() or 'loot_corpse'
        corpse_roi = ctx.rois.get(str(corpse_roi_name))
        if corpse_roi is None:
            _try_dump_looting_pair(gate=gate_name, reason='looting_no_corpse_present', before=before, after=None)
            raise PreflightFailed('looting_no_corpse_present')
        if not _corpse_present_in_roi(frame=before, roi=corpse_roi):
            _try_dump_looting_pair(gate=gate_name, reason='looting_no_corpse_present', before=before, after=None)
            raise PreflightFailed('looting_no_corpse_present')

    loot_action: dict | None = None
    action_ts_ns: int | None = None
    click_x: int | None = None
    click_y: int | None = None

    def _try_dump_visual_suggestions(*, reason: str, before_frame: Frame, player_xy: tuple[int, int] | None) -> list[dict[str, object]]:
        try:
            out_dir = Path(_frames_dir_for_looting_evidence())
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return []

        if player_xy is None:
            return []

        try:
            from runtime.corpse_visual_suggester import suggest_clickxy_for_corpse

            suggestions = suggest_clickxy_for_corpse(
                frame=before_frame,
                player_pos=(int(player_xy[0]), int(player_xy[1])),
                radius_px=int(_env_int('FRBOT_CORPSE_SUGGEST_RADIUS_PX') or 96),
                tile_px=int(_env_int('FRBOT_CORPSE_SUGGEST_TILE_PX') or 32),
                max_suggestions=int(_env_int('FRBOT_CORPSE_SUGGEST_MAX') or 12),
            )
        except Exception:
            return []

        payload: list[dict[str, object]] = [
            {'x': int(s.x), 'y': int(s.y), 'score': float(s.score), 'kind': str(s.kind)} for s in suggestions
        ]
        try:
            (out_dir / 'looting_basic_visual_click_suggestions.json').write_text(
                json.dumps(
                    {
                        'gate': 'looting_basic',
                        'reason': str(reason),
                        'player_pos': {'x': int(player_xy[0]), 'y': int(player_xy[1])},
                        'count': int(len(payload)),
                        'suggestions': payload,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + '\n',
                encoding='utf-8',
            )
        except Exception:
            # Best-effort diagnostics: suggestion dumps must not affect certification.
            pass
        return payload

    # Emit exactly one input.
    try:
        binding.assert_bound()

        mode = str(getattr(getattr(ctx, 'config', object()), 'mode', '') or '').strip().lower()
        if mode == 'real':
            # REAL loot action: certification-safe single action.
            fx = _env_int('FRBOT_LOOTING_BASIC_LOOT_X')
            fy = _env_int('FRBOT_LOOTING_BASIC_LOOT_Y')
            player_x = _env_int('FRBOT_LOOTING_PLAYER_X')
            player_y = _env_int('FRBOT_LOOTING_PLAYER_Y')
            player_xy = None if player_x is None or player_y is None else (int(player_x), int(player_y))

            action = (os.environ.get('FRBOT_LOOTING_BASIC_ACTION', '') or '').strip().lower()
            if not action:
                action = (os.environ.get('FRBOT_TIBIA_LOOT_GESTURE', '') or 'shift_rmb').strip().lower()

            profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
            if profile == 'prod_emergency':
                # Strict certification contract: Alt+Q is the only allowed input action.
                action = 'alt_q'

            gesture = str(action)
            coord_space = (os.environ.get('FRBOT_FRAME_COORD_SPACE', '') or '').strip().lower() or 'frame'

            # Hotkey quick-loot (no ClickXY required).
            if gesture == 'alt_q':
                if not hasattr(input_, 'press_combo'):
                    raise PreflightFailed('looting_input_not_supported')
                # Exactly one input action: Alt+Q.
                input_.press_combo(['alt', 'q'])
                action_ts_ns = int(time.monotonic_ns())
                loot_action = {'kind': 'alt_q', 'ts_ns': int(action_ts_ns)}
            elif gesture == 'key':
                action_ts_ns = int(time.monotonic_ns())
                loot_action = {'kind': 'key', 'key': str(ctx.config.quick_loot_key), 'ts_ns': int(action_ts_ns)}
                if not hasattr(input_, 'press_key'):
                    raise PreflightFailed('looting_input_not_supported')
                input_.press_key(str(ctx.config.quick_loot_key))
            # Cursor-click quick-loot (operator pre-positions cursor; no ClickXY required).
            elif gesture == 'shift_rmb_cursor':
                action_ts_ns = int(time.monotonic_ns())
                loot_action = {'kind': 'shift_rmb_cursor', 'ts_ns': int(action_ts_ns)}
                if not hasattr(input_, 'shift_right_click_cursor'):
                    raise PreflightFailed('looting_input_not_supported')
                input_.shift_right_click_cursor()
            elif gesture == 'rmb_cursor':
                action_ts_ns = int(time.monotonic_ns())
                loot_action = {'kind': 'rmb_cursor', 'ts_ns': int(action_ts_ns)}
                if not hasattr(input_, 'right_click_cursor'):
                    raise PreflightFailed('looting_input_not_supported')
                input_.right_click_cursor()
            else:
                if fx is None or fy is None:
                    # Non-certifying hint: suggest likely corpse tiles near the player.
                    _try_dump_visual_suggestions(reason='looting_click_point_missing', before_frame=before, player_xy=player_xy)
                    _try_dump_looting_pair(gate=gate_name, reason='looting_click_point_missing', before=before, after=None)

                    # Optional: allow auto-using suggestions in non-emergency runs only.
                    if profile != 'prod_emergency' and _env_bool('FRBOT_LOOTING_BASIC_USE_VISUAL_SUGGESTIONS', False):
                        sug = _try_dump_visual_suggestions(reason='looting_click_point_autopick', before_frame=before, player_xy=player_xy)
                        if sug:
                            fx = _safe_int(sug[0].get('x'))
                            fy = _safe_int(sug[0].get('y'))
                        else:
                            raise PreflightFailed('looting_click_point_missing')
                    else:
                        raise PreflightFailed('looting_click_point_missing')

                if fx is None or fy is None:
                    raise PreflightFailed('looting_click_point_missing')

                x, y = int(fx), int(fy)

                action_ts_ns = int(time.monotonic_ns())
                loot_action = {'kind': str(gesture), 'x': int(x), 'y': int(y), 'coord_space': str(coord_space), 'ts_ns': int(action_ts_ns)}

                fw = int(getattr(before, 'width', 0) or 0)
                fh = int(getattr(before, 'height', 0) or 0)

                if gesture == 'shift_rmb':
                    if not hasattr(input_, 'shift_right_click_frame'):
                        raise PreflightFailed('looting_input_not_supported')
                    input_.shift_right_click_frame(int(x), int(y), frame_w=int(fw), frame_h=int(fh))
                elif gesture == 'rmb':
                    if not hasattr(input_, 'right_click_frame'):
                        raise PreflightFailed('looting_input_not_supported')
                    input_.right_click_frame(int(x), int(y), frame_w=int(fw), frame_h=int(fh))
                else:
                    raise PreflightFailed('looting_action_not_configured')
        else:
            # MOCK/other: keep the deterministic single-key loot action.
            action_ts_ns = int(time.monotonic_ns())
            loot_action = {'kind': 'press_key', 'key': str(ctx.config.quick_loot_key), 'ts_ns': int(action_ts_ns)}
            input_.press_key(str(ctx.config.quick_loot_key))
    except Exception as exc:
        _try_dump_looting_pair(gate=gate_name, reason='looting_input_emit_failed', before=before, after=None)
        raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc

    ctx.looting.attempts_used += 1

    is_real = str(getattr(getattr(ctx, 'config', object()), 'mode', '') or '').strip().lower() == 'real'
    gesture_emitted_for_defaults = '' if loot_action is None else str(loot_action.get('kind') or '')

    # Certification contract (strict): no sleeps, no extra inputs.
    # We may take multiple AFTER captures to tolerate render/OBS latency.
    # We never emit more than one input action.
    if is_real and gesture_emitted_for_defaults == 'alt_q':
        profile2 = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        strict_default_attempts = '6' if profile2 == 'prod_emergency' else '4'
        try:
            verify_attempts = int(
                (os.environ.get('FRBOT_LOOTING_BASIC_STRICT_VERIFY_ATTEMPTS', strict_default_attempts) or strict_default_attempts).strip()
                or strict_default_attempts
            )
        except Exception:
            verify_attempts = int(strict_default_attempts)
        verify_attempts = max(1, min(int(verify_attempts), 8))
        verify_delay_ms = 0.0
    else:
        # Legacy behavior for non-cert runs.
        if is_real and gesture_emitted_for_defaults in {'key'}:
            attempts_default = '7'
            delay_default = '350'
        else:
            attempts_default = '4' if is_real else '1'
            delay_default = '150' if is_real else '0'
        try:
            verify_attempts = int((os.environ.get('FRBOT_LOOTING_BASIC_VERIFY_ATTEMPTS', attempts_default) or attempts_default).strip() or attempts_default)
        except Exception:
            verify_attempts = int(attempts_default)
        verify_attempts = max(1, min(int(verify_attempts), 10))

        try:
            raw_delay = (os.environ.get('FRBOT_LOOTING_BASIC_VERIFY_DELAY_MS', delay_default) or delay_default).strip()
            verify_delay_ms = float(raw_delay)
        except Exception:
            verify_delay_ms = float(delay_default)
        verify_delay_ms = max(0.0, min(float(verify_delay_ms), 2000.0))

    after: Frame | None = None
    after_frames_for_stability: list[Frame] = []
    last_inv_after: InventorySnapshot | None = None
    last_delta: InventoryDelta | None = None

    for i in range(int(verify_attempts)):
        after = capture.grab()
        record_after(gate_name, after)
        record_after('runtime', after)
        after_frames_for_stability.append(after)

        inv_pair = read_inventory_pair_binary(before, after, inv_roi)
        if inv_pair is not None:
            _inv_before2, inv_after = inv_pair
            ctx.looting.last_inventory = inv_after
            last_inv_after = inv_after

            delta = diff_inventory(inv_before, inv_after)
            last_delta = delta
            if is_loot_success(delta):
                ctx.looting.items_looted += 1

                # Mandatory prod_emergency evidence: dump binary candidates even on success.
                stable = _try_dump_emergency_candidates(
                    reason='inventory_delta',
                    before_frame=before,
                    after_frames=after_frames_for_stability,
                )
                if (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower() == 'prod_emergency' and not stable:
                    raise PreflightFailed('inventory_overlay_missing')

                # Optional diagnostics: when enabled, persist the before/after frames even on success.
                before_name, after_name = _try_dump_looting_pair(gate=gate_name, reason='inventory_delta', before=before, after=after)

                _try_write_looting_basic_last_result(
                    gate=gate_name,
                    evidence_dir=_frames_dir_for_looting_evidence(),
                    ok=True,
                    outcome_kind='inventory_delta',
                    loot_action=loot_action,
                    verify_attempts=int(verify_attempts),
                    inventory_after_readable=True,
                    inventory_delta_ok=True,
                    inventory_before=inv_before,
                    inventory_after=inv_after,
                    chat_evidence=None,
                    used_chat_fallback=False,
                    before_ppm=before_name,
                    after_ppm=after_name,
                )

                # Enforce mandatory REAL evidence frames BEFORE returning PASS.
                if (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower() == 'prod_emergency':
                    out_dir = Path(_frames_dir_for_looting_evidence())
                    if not before_name or not after_name or (not (out_dir / str(before_name)).exists()) or (not (out_dir / str(after_name)).exists()):
                        raise PreflightFailed(f'{gate_name}_missing_evidence_frames')

                return LootingBasicOutcome(
                    ok=True,
                    evidence_kind='inventory_delta',
                    inventory_before=inv_before,
                    inventory_after=inv_after,
                    delta=delta,
                )

        # Strict certification contract: do not sleep between verification frames.

    inventory_after_readable = last_inv_after is not None

    profile2 = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    allow_chat_fallback = bool(_env_bool('FRBOT_LOOTING_ALLOW_CHAT_FALLBACK', False))
    if profile2 != 'prod_emergency':
        allow_chat_fallback = False

    # Secondary semantic proof: chat loot evidence (supporting-only).
    chat_evidence: LootEvidence | None = None
    try:
        if chat_roi is not None and after is not None:
            act_kind = None if loot_action is None else str(loot_action.get('kind') or '')

            # Strict contract: no extra inputs. We can, however, evaluate multiple AFTER
            # captures to pick the earliest frame that provides valid chat evidence.
            frames_for_chat = list(after_frames_for_stability or [])
            if not frames_for_chat:
                frames_for_chat = [after]

            best_ev: LootEvidence | None = None
            best_frame: Frame | None = None

            def _score(ev: LootEvidence) -> tuple[int, int, int]:
                try:
                    ok = 1 if bool(ev.ok) else 0
                except Exception:
                    ok = 0
                try:
                    reason = str((ev.debug or {}).get('reason') or '')
                except Exception:
                    reason = ''
                in_window = 0 if reason == 'latency_out_of_window' else 1
                try:
                    changed = int((ev.debug or {}).get('changed_pixels') or 0)
                except Exception:
                    changed = 0
                return (int(ok), int(in_window), int(changed))

            for af in frames_for_chat:
                ev = detect_loot_from_chat(
                    before,
                    af,
                    chat_roi,
                    action_kind=act_kind,
                    action_ts_ns=action_ts_ns,
                )
                if best_ev is None or _score(ev) > _score(best_ev):
                    best_ev = ev
                    best_frame = af
                if bool(ev.ok):
                    chat_evidence = ev
                    after = af
                    break

            if chat_evidence is None and best_ev is not None:
                chat_evidence = best_ev
                if best_frame is not None:
                    after = best_frame
            # Chat evidence may only be used as sole proof when inventory is unreadable AFTER,
            # and only when the explicit emergency fallback is enabled.
            if (
                chat_evidence is not None
                and bool(chat_evidence.ok)
                and (not inventory_after_readable)
                and bool(allow_chat_fallback)
            ):
                before_name, after_name = _try_dump_looting_pair(gate=gate_name, reason='chat_delta_inventory_unreadable', before=before, after=after)
                _try_write_looting_basic_last_result(
                    gate=gate_name,
                    evidence_dir=_frames_dir_for_looting_evidence(),
                    ok=True,
                    outcome_kind='chat_delta_inventory_unreadable',
                    loot_action=loot_action,
                    verify_attempts=int(verify_attempts),
                    inventory_after_readable=False,
                    inventory_delta_ok=False,
                    inventory_before=inv_before,
                    inventory_after=None,
                    chat_evidence=chat_evidence,
                    used_chat_fallback=True,
                    before_ppm=before_name,
                    after_ppm=after_name,
                )

                # Enforce mandatory REAL evidence frames BEFORE returning PASS.
                if (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower() == 'prod_emergency':
                    out_dir = Path(_frames_dir_for_looting_evidence())
                    if not before_name or not after_name or (not (out_dir / str(before_name)).exists()) or (not (out_dir / str(after_name)).exists()):
                        raise PreflightFailed(f'{gate_name}_missing_evidence_frames')

                return LootingBasicOutcome(
                    ok=True,
                    evidence_kind='chat_delta_inventory_unreadable',
                    inventory_before=inv_before,
                    inventory_after=None,
                    delta=None,
                )
    except Exception:
        chat_evidence = None

    # If inventory is unreadable and we did not get a bound chat confirmation, abort.
    if not inventory_after_readable:
        before_name, after_name = _try_dump_looting_pair(gate=gate_name, reason='looting_inventory_unreadable', before=before, after=after)
        _try_write_looting_basic_last_result(
            gate=gate_name,
            evidence_dir=_frames_dir_for_looting_evidence(),
            ok=False,
            outcome_kind='looting_inventory_unreadable',
            loot_action=loot_action,
            verify_attempts=int(verify_attempts),
            inventory_after_readable=False,
            inventory_delta_ok=False,
            inventory_before=inv_before,
            inventory_after=None,
            chat_evidence=chat_evidence,
            used_chat_fallback=False,
            before_ppm=before_name,
            after_ppm=after_name,
        )
        raise PreflightFailed('looting_inventory_unreadable')

    # Preflight check: if right-click opened a context menu (no quick-loot), abort.
    click_x = _safe_int(None if loot_action is None else loot_action.get('x'))
    click_y = _safe_int(None if loot_action is None else loot_action.get('y'))
    try:
        if after is not None and click_x is not None and click_y is not None:
            gesture2 = '' if loot_action is None else str(loot_action.get('kind') or '')
            if gesture2 in {'shift_rmb', 'rmb'} and _context_menu_like_opened(before_frame=before, after_frame=after, x=int(click_x), y=int(click_y)):
                _try_dump_click_overlay(reason='looting_action_not_configured', frame=before, x=click_x, y=click_y)
                raise PreflightFailed('looting_action_not_configured')
    except PreflightFailed:
        raise
    except Exception:
        # Best-effort precondition check: if context-menu detection fails, fall back to
        # primary semantic evidence rules (inventory/chat delta).
        pass
    # Optional explicit quick-loot validation mode: if we emitted Shift+RMB and
    # observed no semantic evidence, label it as "quick_loot_not_effective".
    profile3 = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    validate_quick_loot = _env_bool('FRBOT_VALIDATE_QUICK_LOOT', profile3 == 'prod_emergency')
    gesture_emitted = '' if loot_action is None else str(loot_action.get('kind') or '')
    if validate_quick_loot and gesture_emitted in {'shift_rmb', 'alt_q', 'key'}:
        # Diagnóstico (no PASS):
        # - Si hubo delta en chat dentro de ventana, es probable que Quick Loot sí se haya ejecutado
        #   pero no tenemos evidencia semántica (inventario), así que no podemos certificar.
        # - Si no hubo delta en chat, tratamos como binding/acción no efectiva.
        if chat_evidence is not None:
            # If the chat ROI changed at all, we avoid claiming the input didn't work;
            # it's still not certifiable without a semantic inventory delta.
            try:
                dbg = dict(chat_evidence.debug or {})
            except Exception:
                dbg = {}
            try:
                changed_pixels = int(dbg.get('changed_pixels') or 0)
            except Exception:
                changed_pixels = 0
            if bool(chat_evidence.ok) or int(changed_pixels) > 0:
                reason = 'looting_basic_not_confirmed'
            else:
                reason = 'quick_loot_not_effective'
        else:
            reason = 'quick_loot_not_effective'
    else:
        reason = 'looting_no_inventory_delta'

    before_name, after_name = _try_dump_looting_pair(gate=gate_name, reason=str(reason), before=before, after=after)
    _try_write_looting_basic_last_result(
        gate=gate_name,
        evidence_dir=_frames_dir_for_looting_evidence(),
        ok=False,
        outcome_kind=str(reason),
        loot_action=loot_action,
        verify_attempts=int(verify_attempts),
        inventory_after_readable=True,
        inventory_delta_ok=False,
        inventory_before=inv_before,
        inventory_after=last_inv_after,
        chat_evidence=chat_evidence,
        used_chat_fallback=False,
        before_ppm=before_name,
        after_ppm=after_name,
    )
    _try_dump_click_overlay(reason=str(reason), frame=before, x=click_x, y=click_y)
    _try_dump_emergency_candidates(
        reason=str(reason),
        before_frame=before,
        after_frames=after_frames_for_stability,
    )

    # Note: do not fail based on overlay stability here; this path already fails on lack of semantic evidence.

    failure = PreflightFailed(str(reason))
    try:
        setattr(
            failure,
            'details',
            {
            'evidence_source': 'chat' if (chat_evidence is not None and chat_evidence.ok) else 'inventory',
            'loot_action': loot_action,
            'inventory_before': {
                'slot_counts': dict(inv_before.slot_counts or {}),
                'capacity_used': inv_before.capacity_used,
            },
            'inventory_after': {}
            if last_inv_after is None
            else {
                'slot_counts': dict(last_inv_after.slot_counts or {}),
                'capacity_used': last_inv_after.capacity_used,
            },
            'chat_evidence': None
            if chat_evidence is None
            else {
                'ok': bool(chat_evidence.ok),
                'delta_items': int(chat_evidence.delta_items),
                'delta_gold': int(chat_evidence.delta_gold),
                'debug': dict(chat_evidence.debug or {}),
            },
            'delta': None
            if last_delta is None
            else {
                'slot_deltas': dict(last_delta.slot_deltas or {}),
                'capacity_used_delta': int(last_delta.capacity_used_delta),
            },
            'verify': {
                'attempts': int(verify_attempts),
                'delay_ms': float(verify_delay_ms),
            },
            },
        )
    except Exception:
        # Best-effort: attaching debug details must not change the failure reason.
        pass
    raise failure
