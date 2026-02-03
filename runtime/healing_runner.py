from __future__ import annotations

import os
import time

from contracts.capture import CaptureAdapter, Frame
from contracts.errors import PreflightFailed
from contracts.healing import HealIntent
from contracts.input import InputAdapter
from contracts.runtime import RuntimeContext
from contracts.window import WindowBindingAdapter
from diagnostics.last_frames import record_after, record_before
from runtime.healing_semantics import (
    PercentRead,
    detect_cooldown_marker,
    parse_rgb_triplet,
    read_bar_percent,
    read_hp_mp_text_pair,
    read_percent_with_consistency,
    read_text_percent,
)


def _read_hp_mp(ctx: RuntimeContext, frame: Frame) -> tuple[float, float, str]:
    hp_mp_roi = ctx.rois.get(getattr(ctx.config, 'hp_mp_roi', 'hp_mp'))
    if hp_mp_roi is not None:
        pair = read_hp_mp_text_pair(frame, hp_mp_roi)
        if pair is None:
            raise PreflightFailed('hp_mp_unreadable')
        hp, mp = pair
        return float(hp.value), float(mp.value), 'hp_mp'

    hp_bar = ctx.rois.get(ctx.config.hp_bar_roi)
    hp_text = ctx.rois.get(ctx.config.hp_text_roi)
    mp_bar = ctx.rois.get(ctx.config.mp_bar_roi)
    mp_text = ctx.rois.get(ctx.config.mp_text_roi)

    hp_read: PercentRead | None = read_percent_with_consistency(
        bar=(read_bar_percent(frame, hp_bar, channel='r') if hp_bar is not None else None),
        text=(read_text_percent(frame, hp_text) if hp_text is not None else None),
        tol=float(ctx.config.heal_consistency_tol),
    )
    mp_read: PercentRead | None = read_percent_with_consistency(
        bar=(read_bar_percent(frame, mp_bar, channel='b') if mp_bar is not None else None),
        text=(read_text_percent(frame, mp_text) if mp_text is not None else None),
        tol=float(ctx.config.heal_consistency_tol),
    )

    if hp_read is None or mp_read is None:
        raise PreflightFailed('hp_mp_unreadable')

    if hp_read.source == 'bar+text' or mp_read.source == 'bar+text':
        src = 'bar+text'
    elif hp_read.source == 'text' or mp_read.source == 'text':
        src = 'text'
    else:
        src = 'bar'

    return float(hp_read.value), float(mp_read.value), str(src)


def _cooldown_ok_to_cast(ctx: RuntimeContext, frame: Frame) -> bool:
    roi = ctx.rois.get(ctx.config.heal_cooldown_roi)
    if roi is None:
        # PROD-EMERGENCY contract may omit cooldown ROI.
        profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        if profile == 'prod_emergency':
            return True
        raise PreflightFailed('heal_cooldown_unknown')

    marker = parse_rgb_triplet(os.environ.get('FRBOT_HEAL_COOLDOWN_RGB', '255,255,0') or '255,255,0', default=(255, 255, 0))
    tol = int(os.environ.get('FRBOT_HEAL_COOLDOWN_TOL', '0') or '0')

    cd = detect_cooldown_marker(frame, roi, marker_rgb=marker, tol=tol)
    if cd is None:
        raise PreflightFailed('heal_cooldown_unknown')

    # ok only if NOT in cooldown
    return cd is False


def _feedback_visible(ctx: RuntimeContext, frame: Frame) -> bool:
    roi = ctx.rois.get(ctx.config.heal_feedback_roi)
    if roi is None:
        return False
    # Same marker detection mechanism (green by default).
    marker = parse_rgb_triplet(os.environ.get('FRBOT_HEAL_FEEDBACK_RGB', '0,255,0') or '0,255,0', default=(0, 255, 0))
    tol = int(os.environ.get('FRBOT_HEAL_FEEDBACK_TOL', '0') or '0')
    cd = detect_cooldown_marker(frame, roi, marker_rgb=marker, tol=tol)
    return bool(cd)


def execute_heal_intent(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    intent: HealIntent,
) -> bool:
    """Execute a single heal intent with evidence-or-abort.

    Returns True only if we observe objective evidence (HP up OR cooldown appears OR feedback visible).
    Consumes attempts and aborts deterministically when exceeded.
    """

    now_ms = int(time.monotonic_ns() // 1_000_000)
    if ctx.healing.attempt_count == 0:
        ctx.healing.attempt_started_ts_ms = now_ms

    if ctx.healing.attempt_count >= int(ctx.config.max_attempts_per_heal):
        raise PreflightFailed('heal_unverified')
    if ctx.healing.attempt_started_ts_ms and (now_ms - ctx.healing.attempt_started_ts_ms) >= int(ctx.config.max_time_ms_per_heal):
        raise PreflightFailed('heal_unverified')

    try:
        binding.assert_bound()
    except Exception:
        raise PreflightFailed('healing_window_binding_lost')

    before = capture.grab()
    record_before('healing', before)
    before_hp, before_mp, src = _read_hp_mp(ctx, before)

    # Cooldown must be observable before casting.
    if not _cooldown_ok_to_cast(ctx, before):
        return False

    try:
        binding.assert_bound()
        input_.press_key(str(intent.key))
    except Exception as exc:
        raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc

    after = capture.grab()
    record_after('healing', after)
    after_hp, after_mp, _ = _read_hp_mp(ctx, after)

    hp_up = (after_hp - before_hp) >= float(intent.expected.hp_increase_min)

    # Cooldown evidence: marker present after cast.
    cooldown_roi = ctx.rois.get(ctx.config.heal_cooldown_roi)
    if cooldown_roi is None:
        raise PreflightFailed('heal_cooldown_unknown')
    marker = parse_rgb_triplet(os.environ.get('FRBOT_HEAL_COOLDOWN_RGB', '255,255,0') or '255,255,0', default=(255, 255, 0))
    tol = int(os.environ.get('FRBOT_HEAL_COOLDOWN_TOL', '0') or '0')
    cooldown_after = detect_cooldown_marker(after, cooldown_roi, marker_rgb=marker, tol=tol)
    if cooldown_after is None:
        raise PreflightFailed('heal_cooldown_unknown')

    feedback = _feedback_visible(ctx, after)

    evidence_ok = bool(hp_up) or bool(cooldown_after) or bool(feedback)

    ctx.healing.last.hp_percent = float(after_hp)
    ctx.healing.last.mp_percent = float(after_mp)
    ctx.healing.last.source = src  # type: ignore[assignment]
    ctx.healing.last.confidence = 1.0

    if not evidence_ok:
        ctx.healing.attempt_count += 1
        if ctx.healing.attempt_count >= int(ctx.config.max_attempts_per_heal):
            raise PreflightFailed('heal_unverified')
        return False

    ctx.healing.attempt_count = 0
    ctx.healing.attempt_started_ts_ms = 0
    return True
