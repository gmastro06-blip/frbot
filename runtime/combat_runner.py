from __future__ import annotations

import os
import time

from contracts.capture import CaptureAdapter, Frame
from contracts.combat import CombatIntent
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import RuntimeContext
from contracts.window import WindowBindingAdapter
from runtime.battle_list_semantics import crop_roi_rgb, detect_battle_list
from runtime.combat_semantics import detect_damage_feedback, read_target_hp_percent
from runtime.healing_runner import _read_hp_mp
from runtime.healing_semantics import detect_cooldown_marker, parse_rgb_triplet


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
        raise PreflightFailed('combat_target_not_locked')
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

    tf_name = _decode_name_from_target_frame(tf_rgb, int(tf_roi.width))
    if tf_name != e.name:
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


def execute_combat_intent(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    intent: CombatIntent,
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

    before = capture.grab()

    # Hard invariants before casting.
    locked_name = _get_locked_target_name(ctx, before)

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
        input_.press_key(str(intent.key))
    except Exception as exc:
        raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc
    ctx.combat.inputs_sent += 1

    after = capture.grab()

    # Must still be locked after attack.
    locked_name_after = _get_locked_target_name(ctx, after)
    if locked_name_after != locked_name:
        raise PreflightFailed('combat_target_not_locked')

    after_hp = read_target_hp_percent(after, hp_roi)
    if after_hp is None:
        raise PreflightFailed('combat_ambiguous_result')

    hp_drop = float(before_hp.value) - float(after_hp.value)

    cooldown_active_after = _read_attack_cooldown_active(ctx, after)

    # Optional explicit feedback.
    feedback_ok = False
    feedback_roi = ctx.rois.get(ctx.config.combat_feedback_roi)
    if feedback_roi is not None:
        marker = parse_rgb_triplet(
            os.environ.get('FRBOT_COMBAT_FEEDBACK_RGB', '0,255,255') or '0,255,255',
            default=(0, 255, 255),
        )
        tol = int(os.environ.get('FRBOT_COMBAT_FEEDBACK_TOL', '0') or '0')
        fb = detect_damage_feedback(after, feedback_roi, marker_rgb=marker, tol=tol)
        if fb is None:
            raise PreflightFailed('combat_ambiguous_result')
        feedback_ok = bool(fb)

    # Accept exactly one of the semantic evidence categories.
    evidence_ok = (
        (hp_drop >= float(intent.expected.target_hp_decrease_min))
        or bool(cooldown_active_after)
        or bool(feedback_ok)
    )

    ctx.combat.last_target_hp = float(after_hp.value)

    if not evidence_ok:
        ctx.combat.attempt_count += 1
        raise PreflightFailed('combat_unverified_attack')

    ctx.combat.attempt_count = 0
    ctx.combat.attempt_started_ts_ms = 0
    return True
