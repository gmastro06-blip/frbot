from __future__ import annotations

import os
import time

from contracts.capture import CaptureAdapter, Frame
from contracts.combat import CombatIntent
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import Rect, RuntimeContext
from contracts.window import WindowBindingAdapter
from diagnostics.last_frames import record_after, record_before
from runtime.battle_list_semantics import crop_roi_rgb, detect_battle_list
from runtime.combat_semantics import detect_damage_feedback, read_target_hp_percent
from runtime.healing_runner import _read_hp_mp
from runtime.healing_semantics import detect_cooldown_marker, parse_rgb_triplet
from runtime.event_correlation import attach_snapshot, new_event, validate
from runtime.pacing import wait_until_ns


def _decode_name_from_target_frame(rgb: bytes, width: int) -> str:
    # Same encoding as battle list mock OCR.
    out: list[int] = []
    for i in range(12):
        idx = ((2 * width) + (2 + i)) * 3
        if idx < 0 or idx + 2 >= len(rgb):
            break
        r = rgb[idx]
        g = rgb[idx + 1]
        b = rgb[idx + 2]
        if g != 0 or b != 0:
            return ''
        if r == 0:
            break
        out.append(int(r))
    try:
        return bytes(out).decode('ascii', errors='ignore').strip()
    except Exception:
        return ''


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _target_frame_visible(rgb: bytes) -> bool:
    return any(b != 0 for b in rgb[: min(len(rgb), 300)])


def _get_locked_target_name(ctx: RuntimeContext, frame: Frame) -> str:
    """Verify target is locked via Battle List highlight + target frame identity.

    Returns the verified target name or raises:
    - target_lost
    - combat_invalid_state (ambiguity)
    """

    battle_roi = ctx.rois.get(ctx.config.battle_list_roi)
    tf_roi = ctx.rois.get(ctx.config.target_frame_roi)
    if battle_roi is None or tf_roi is None:
        raise PreflightFailed('combat_ambiguous_result')

    obs = detect_battle_list(frame, battle_roi)
    if obs is None:
        raise PreflightFailed('combat_ambiguous_result')

    highlighted = [e for e in obs.entries if bool(e.highlighted)]
    if not highlighted:
        # Fallback: some real client themes/settings may not present a stable Battle List highlight.
        # If the target frame is visible AND the target HP bar is readable, treat as locked.
        tf_rgb = crop_roi_rgb(frame, tf_roi)
        if not tf_rgb or not _target_frame_visible(tf_rgb):
            raise PreflightFailed('combat_target_not_locked')

        hp_roi = ctx.rois.get(getattr(ctx.config, 'target_hp_bar_roi', '') or '')
        if hp_roi is None or read_target_hp_percent(frame, hp_roi) is None:
            raise PreflightFailed('combat_target_not_locked')

        # Store a stable rect for downstream consumers.
        ctx.targeting.target.target_rect = Rect(
            x=int(tf_roi.x),
            y=int(tf_roi.y),
            width=int(tf_roi.width),
            height=int(tf_roi.height),
        )
        return 'target'
    if len(highlighted) != 1:
        raise PreflightFailed('combat_ambiguous_result')

    e = highlighted[0]
    if not (e.name and e.name.strip()):
        raise PreflightFailed('combat_ambiguous_result')

    tf_rgb = crop_roi_rgb(frame, tf_roi)
    if not tf_rgb:
        raise PreflightFailed('combat_target_not_locked')
    if not _target_frame_visible(tf_rgb):
        raise PreflightFailed('combat_target_not_locked')

    # Mock OCR decoding is only meaningful for synthetic frames.
    # For REAL frames, decoding arbitrary pixels can yield rare false positives;
    # treat the target frame identity check as visibility-only unless explicitly required.
    mock_rows_env_present = os.environ.get('FRBOT_MOCK_BATTLE_LIST_ROWS') is not None
    allow_mock_ocr = (str(os.environ.get('FRBOT_MODE') or '').strip().lower() == 'mock') or bool(mock_rows_env_present)

    tf_name = _decode_name_from_target_frame(tf_rgb, int(tf_roi.width)) if allow_mock_ocr else ''
    if tf_name:
        if tf_name != e.name:
            raise PreflightFailed('combat_target_not_locked')
    else:
        if _env_bool('FRBOT_REQUIRE_TARGET_FRAME_NAME', False):
            raise PreflightFailed('combat_target_not_locked')

    # Store a verified rect for downstream consumers.
    ctx.targeting.target.target_rect = e.screen_bbox

    return str(e.name)


def _read_attack_cooldown_active(ctx: RuntimeContext, frame: Frame) -> bool:
    roi = ctx.rois.get(ctx.config.combat_cooldown_roi)
    if roi is None:
        raise PreflightFailed('combat_ambiguous_result')

    marker = parse_rgb_triplet(
        os.environ.get('FRBOT_COMBAT_COOLDOWN_RGB', '255,255,0') or '255,255,0',
        default=(255, 255, 0),
    )
    tol = int(os.environ.get('FRBOT_COMBAT_COOLDOWN_TOL', '0') or '0')

    cd = detect_cooldown_marker(frame, roi, marker_rgb=marker, tol=tol)
    if cd is None:
        raise PreflightFailed('combat_ambiguous_result')

    return cd is True


def _changed_ratio(before_rgb: bytes, after_rgb: bytes, *, px_tol: int) -> float:
    if not before_rgb or not after_rgb or len(before_rgb) != len(after_rgb):
        return 0.0
    t = int(px_tol)
    changed = 0
    npx = max(1, len(before_rgb) // 3)
    for i in range(0, len(before_rgb), 3):
        if (
            abs(int(before_rgb[i + 0]) - int(after_rgb[i + 0])) > t
            or abs(int(before_rgb[i + 1]) - int(after_rgb[i + 1])) > t
            or abs(int(before_rgb[i + 2]) - int(after_rgb[i + 2])) > t
        ):
            changed += 1
    return float(changed) / float(npx)


def execute_combat_intent(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    intent: CombatIntent,
    gate: str = 'combat',
) -> bool:
    """Execute a single attack intent with semantic evidence-or-abort.

    Evidence accepted:
    - target HP decreases by >= expected.target_hp_decrease_min
    - OR explicit damage feedback marker visible

    On no evidence, consumes attempt.
    """

    now_ms = int(time.monotonic_ns() // 1_000_000)
    if ctx.combat.attempt_count == 0:
        ctx.combat.attempt_started_ts_ms = now_ms

    if ctx.combat.attempt_count >= int(ctx.config.max_attempts_per_target):
        raise PreflightFailed('combat_max_attempts')
    if ctx.combat.attempt_started_ts_ms and (now_ms - ctx.combat.attempt_started_ts_ms) >= int(ctx.config.max_time_ms_per_target):
        raise PreflightFailed('combat_timeout')

    try:
        binding.assert_bound()
    except Exception:
        raise PreflightFailed('combat_ambiguous_result')

    event = new_event(
        gate=str(gate),
        intent={
            'type': 'combat_key',
            'key': str(getattr(intent, 'key', '') or ''),
        },
    )

    before_ts_ns = int(time.monotonic_ns())
    attach_snapshot(event, stage='before', ts_ns=before_ts_ns, status=binding.snapshot())
    before = capture.grab()
    record_before(str(gate), before)

    # Hard invariants before casting.
    locked_name = _get_locked_target_name(ctx, before)
    before_lock_rect = ctx.targeting.target.target_rect

    # Some keybinds are semantic "attack next target" and will legitimately change the locked target.
    key_norm = str(intent.key or '').strip().lower()
    is_next_target_key = key_norm in {'avpag', 'pgdn', 'pagedown'}

    # HP/MP must be readable (reuse healing contract).
    _read_hp_mp(ctx, before)

    cooldown_active_before = _read_attack_cooldown_active(ctx, before)
    if cooldown_active_before:
        raise PreflightFailed('combat_on_cooldown')

    hp_roi = ctx.rois.get(ctx.config.target_hp_bar_roi)
    if hp_roi is None:
        raise PreflightFailed('combat_ambiguous_result')
    before_hp = read_target_hp_percent(before, hp_roi)
    if before_hp is None:
        raise PreflightFailed('combat_ambiguous_result')

    # Execute exactly one input.
    try:
        binding.assert_bound()
        input_ts_ns = int(time.monotonic_ns())
        attach_snapshot(event, stage='input', ts_ns=input_ts_ns, status=binding.snapshot())
        input_.press_key(str(intent.key))
    except Exception as exc:
        raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc
    ctx.combat.inputs_sent += 1

    # Real UI and OBS capture may lag behind inputs; add bounded pacing.
    try:
        post_attack_ms = int(os.environ.get('FRBOT_POST_ATTACK_DELAY_MS', '150') or '150')
    except Exception:
        post_attack_ms = 150
    if post_attack_ms > 0:
        wait_until_ns(int(time.monotonic_ns() + (int(post_attack_ms) * 1_000_000)))

    # Sample multiple AFTER frames in a bounded window to tolerate UI/capture latency.
    try:
        after_window_ms = int(os.environ.get('FRBOT_COMBAT_AFTER_WINDOW_MS', '900') or '900')
    except Exception:
        after_window_ms = 900
    try:
        after_poll_ms = int(os.environ.get('FRBOT_COMBAT_AFTER_POLL_MS', '120') or '120')
    except Exception:
        after_poll_ms = 120
    after_window_ms = max(0, min(int(after_window_ms), 3000))
    after_poll_ms = max(50, min(int(after_poll_ms), 500))

    # Pre-crop BEFORE ROIs once.
    battle_roi = ctx.rois.get(ctx.config.battle_list_roi)
    battle_before = crop_roi_rgb(before, battle_roi) if battle_roi is not None else b''
    cd_roi = ctx.rois.get(ctx.config.combat_cooldown_roi)
    cd_before = crop_roi_rgb(before, cd_roi) if cd_roi is not None else b''
    feedback_roi = ctx.rois.get(ctx.config.combat_feedback_roi)
    fb_before = crop_roi_rgb(before, feedback_roi) if feedback_roi is not None else b''

    try:
        cd_px_tol = int(os.environ.get('FRBOT_COMBAT_COOLDOWN_DELTA_PX_TOL', '15') or '15')
        cd_ratio_thr = float(os.environ.get('FRBOT_COMBAT_COOLDOWN_DELTA_RATIO_MIN', '0.003') or '0.003')
    except Exception:
        cd_px_tol = 15
        cd_ratio_thr = 0.003
    try:
        fb_px_tol = int(os.environ.get('FRBOT_COMBAT_FEEDBACK_DELTA_PX_TOL', '15') or '15')
        fb_ratio_thr = float(os.environ.get('FRBOT_COMBAT_FEEDBACK_DELTA_RATIO_MIN', '0.0015') or '0.0015')
    except Exception:
        fb_px_tol = 15
        fb_ratio_thr = 0.0015

    try:
        bl_px_tol = int(os.environ.get('FRBOT_COMBAT_BATTLE_LIST_DELTA_PX_TOL', '15') or '15')
        bl_ratio_thr = float(os.environ.get('FRBOT_COMBAT_BATTLE_LIST_DELTA_RATIO_MIN', '0.02') or '0.02')
    except Exception:
        bl_px_tol = 15
        bl_ratio_thr = 0.02

    marker_fb = parse_rgb_triplet(
        os.environ.get('FRBOT_COMBAT_FEEDBACK_RGB', '0,255,255') or '0,255,255',
        default=(0, 255, 255),
    )
    tol_fb = int(os.environ.get('FRBOT_COMBAT_FEEDBACK_TOL', '0') or '0')

    deadline_ns = int(time.monotonic_ns() + (int(after_window_ms) * 1_000_000))
    next_poll_ns = int(time.monotonic_ns())
    last_after: Frame | None = None
    winning_after: Frame | None = None
    saw_locked = False
    evidence_ok_any = False

    locked_name_after_last: str | None = None

    while True:
        after = capture.grab()
        last_after = after

        # Must still be locked.
        try:
            locked_name_after = _get_locked_target_name(ctx, after)
            locked_name_after_last = str(locked_name_after)

            # For next-target keys: OCR names may be generic (row_1). Accept a highlight-row move.
            if is_next_target_key:
                after_lock_rect = ctx.targeting.target.target_rect
                if after_lock_rect is not None and before_lock_rect is not None and after_lock_rect != before_lock_rect:
                    record_after(str(gate), after)
                    winning_after = after
                    evidence_ok_any = True
                    saw_locked = True
                    break

            # For next-target keys: a verified lock change is acceptable evidence.
            if is_next_target_key and locked_name_after != locked_name:
                record_after(str(gate), after)
                winning_after = after
                evidence_ok_any = True
                saw_locked = True
                break

            # For regular attack keys: must remain locked to the same target.
            if (not is_next_target_key) and locked_name_after != locked_name:
                raise PreflightFailed('combat_target_not_locked')
            saw_locked = True
        except PreflightFailed:
            # If the target is briefly not readable/locked, keep sampling until the window expires.
            if time.monotonic_ns() >= deadline_ns:
                break
            next_poll_ns += int(after_poll_ms) * 1_000_000
            wait_until_ns(int(next_poll_ns))
            continue

        after_hp = read_target_hp_percent(after, hp_roi)
        if after_hp is None:
            raise PreflightFailed('combat_ambiguous_result')

        hp_drop = float(before_hp.value) - float(after_hp.value)

        cooldown_active_after = _read_attack_cooldown_active(ctx, after)

        cooldown_delta_ok = False
        if cd_roi is not None:
            cd_after = crop_roi_rgb(after, cd_roi)
            cd_ratio = _changed_ratio(cd_before, cd_after, px_tol=int(cd_px_tol))
            cooldown_delta_ok = bool(cd_ratio >= float(cd_ratio_thr))

        feedback_ok = False
        feedback_delta_ok = False
        if feedback_roi is not None:
            fb = detect_damage_feedback(after, feedback_roi, marker_rgb=marker_fb, tol=tol_fb)
            if fb is None:
                raise PreflightFailed('combat_ambiguous_result')
            feedback_ok = bool(fb)

            fb_after = crop_roi_rgb(after, feedback_roi)
            fb_ratio = _changed_ratio(fb_before, fb_after, px_tol=int(fb_px_tol))
            feedback_delta_ok = bool(fb_ratio >= float(fb_ratio_thr))

        battle_list_delta_ok = False
        if is_next_target_key and battle_roi is not None and battle_before:
            battle_after = crop_roi_rgb(after, battle_roi)
            bl_ratio = _changed_ratio(battle_before, battle_after, px_tol=int(bl_px_tol))
            battle_list_delta_ok = bool(bl_ratio >= float(bl_ratio_thr))

        evidence_ok = (
            (hp_drop >= float(intent.expected.target_hp_decrease_min))
            or bool(cooldown_active_after)
            or bool(cooldown_delta_ok)
            or bool(feedback_ok)
            or bool(feedback_delta_ok)
            or bool(battle_list_delta_ok)
        )

        ctx.combat.last_target_hp = float(after_hp.value)

        if evidence_ok:
            record_after(str(gate), after)
            winning_after = after
            evidence_ok_any = True
            break

        if time.monotonic_ns() >= deadline_ns:
            break
        next_poll_ns += int(after_poll_ms) * 1_000_000
        wait_until_ns(int(next_poll_ns))

    if last_after is not None:
        record_after(str(gate), last_after)
        after_ts_ns = int(time.monotonic_ns())
        attach_snapshot(event, stage='after', ts_ns=after_ts_ns, status=binding.snapshot())
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

    if not saw_locked:
        raise PreflightFailed('combat_target_not_locked')

    if evidence_ok_any and winning_after is not None:
        ctx.combat.attempt_count = 0
        ctx.combat.attempt_started_ts_ms = 0
        return True

    # If we didn't break on evidence_ok above, we failed to verify within the window.
    ctx.combat.attempt_count += 1
    raise PreflightFailed('combat_unverified_attack')

    ctx.combat.attempt_count = 0
    ctx.combat.attempt_started_ts_ms = 0
    return True
