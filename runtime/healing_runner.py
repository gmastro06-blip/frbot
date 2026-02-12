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
from runtime.pacing import wait_until_ns
from runtime.event_correlation import attach_snapshot, new_event, validate


def _read_hp_mp(ctx: RuntimeContext, frame: Frame) -> tuple[float, float, str]:
    hp_mp_roi = ctx.rois.get(getattr(ctx.config, 'hp_mp_roi', 'hp_mp'))
    if hp_mp_roi is not None and int(getattr(hp_mp_roi, 'height', 0) or 0) == 1:
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
    gate: str = 'healing',
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

    event = new_event(
        gate=str(gate),
        intent={
            'type': 'healing_key',
            'key': str(getattr(intent, 'key', '') or ''),
        },
    )

    before_ts_ns = int(time.monotonic_ns())
    attach_snapshot(event, stage='before', ts_ns=before_ts_ns, status=binding.snapshot())
    before = capture.grab()
    record_before(str(gate), before)
    before_hp, before_mp, src = _read_hp_mp(ctx, before)

    # Cooldown must be observable before casting.
    if not _cooldown_ok_to_cast(ctx, before):
        return False

    try:
        binding.assert_bound()
        input_ts_ns = int(time.monotonic_ns())
        attach_snapshot(event, stage='input', ts_ns=input_ts_ns, status=binding.snapshot())
        input_.press_key(str(intent.key))
    except Exception as input_exc:
        raise PreflightFailed(f'input emit failed: {type(input_exc).__name__}: {input_exc}') from input_exc

    def _delta_ratio_ok(before_rgb: bytes, after_rgb: bytes) -> bool:
        if not before_rgb or not after_rgb or len(before_rgb) != len(after_rgb):
            return False
        try:
            px_tol = int(os.environ.get('FRBOT_HEAL_COOLDOWN_DELTA_PX_TOL', '15') or '15')
        except Exception:
            px_tol = 15
        try:
            ratio_thr = float(os.environ.get('FRBOT_HEAL_COOLDOWN_DELTA_RATIO_MIN', '0.008') or '0.008')
        except Exception:
            ratio_thr = 0.008

        changed = 0
        npx = max(1, len(before_rgb) // 3)
        for i in range(0, len(before_rgb) - 2, 3):
            if (
                abs(int(before_rgb[i]) - int(after_rgb[i])) > px_tol
                or abs(int(before_rgb[i + 1]) - int(after_rgb[i + 1])) > px_tol
                or abs(int(before_rgb[i + 2]) - int(after_rgb[i + 2])) > px_tol
            ):
                changed += 1
        return (float(changed) / float(npx)) >= float(ratio_thr)

    # Real UI and OBS capture may lag behind inputs; poll for evidence with a bounded window.
    try:
        max_wait_ms = int(os.environ.get('FRBOT_POST_HEAL_DELAY_MS', '350') or '350')
    except Exception:
        max_wait_ms = 350
    try:
        poll_ms = int(os.environ.get('FRBOT_POST_HEAL_POLL_MS', '60') or '60')
    except Exception:
        poll_ms = 60
    max_wait_ms = max(0, int(max_wait_ms))
    poll_ms = max(10, int(poll_ms))

    cooldown_roi = ctx.rois.get(ctx.config.heal_cooldown_roi)
    if cooldown_roi is None:
        raise PreflightFailed('heal_cooldown_unknown')
    feedback_roi = ctx.rois.get(ctx.config.heal_feedback_roi)

    # Pre-crop "before" ROIs for delta checks.
    before_cd_rgb = b''
    before_fb_rgb = b''
    allow_delta = str(os.environ.get('FRBOT_HEAL_ALLOW_COOLDOWN_ROI_DELTA', '1') or '1').strip().lower() not in {'', '0', 'false', 'no', 'off'}
    if allow_delta:
        try:
            from runtime.healing_semantics import _crop_rgb  # type: ignore

            before_cd_rgb = _crop_rgb(before, cooldown_roi)
            if feedback_roi is not None:
                before_fb_rgb = _crop_rgb(before, feedback_roi)
        except Exception:
            before_cd_rgb = b''
            before_fb_rgb = b''

    marker = parse_rgb_triplet(os.environ.get('FRBOT_HEAL_COOLDOWN_RGB', '255,255,0') or '255,255,0', default=(255, 255, 0))
    tol = int(os.environ.get('FRBOT_HEAL_COOLDOWN_TOL', '0') or '0')

    deadline_ns = int(time.monotonic_ns() + (int(max_wait_ms) * 1_000_000))
    after: Frame | None = None
    after_hp = float(before_hp)
    after_mp = float(before_mp)
    cooldown_after: bool | None = False
    delta_ok = False
    feedback = False

    while True:
        # Minimum pacing between samples.
        if poll_ms > 0:
            wait_until_ns(int(time.monotonic_ns() + (int(poll_ms) * 1_000_000)))

        cand = capture.grab()
        cand_hp, cand_mp, _ = _read_hp_mp(ctx, cand)

        # Cooldown evidence (marker).
        cd_marker = detect_cooldown_marker(cand, cooldown_roi, marker_rgb=marker, tol=tol)
        if cd_marker is None:
            raise PreflightFailed('heal_cooldown_unknown')

        # Cooldown evidence (theme-agnostic delta), plus optional feedback delta.
        cd_delta = False
        fb_delta = False
        if allow_delta:
            try:
                from runtime.healing_semantics import _crop_rgb  # type: ignore

                if before_cd_rgb:
                    cand_cd = _crop_rgb(cand, cooldown_roi)
                    cd_delta = _delta_ratio_ok(before_cd_rgb, cand_cd)
                if feedback_roi is not None and before_fb_rgb:
                    cand_fb = _crop_rgb(cand, feedback_roi)
                    fb_delta = _delta_ratio_ok(before_fb_rgb, cand_fb)
            except Exception:
                cd_delta = False
                fb_delta = False

        fb_visible = _feedback_visible(ctx, cand)

        # Primary evidence: HP up OR MP down OR cooldown marker/delta OR feedback visible/delta.
        hp_up = (float(cand_hp) - float(before_hp)) >= float(intent.expected.hp_increase_min)
        try:
            mp_dec_min = float(os.environ.get('FRBOT_HEAL_MP_DECREASE_MIN', '0.005') or '0.005')
        except Exception:
            mp_dec_min = 0.005
        mp_down = (float(before_mp) - float(cand_mp)) >= float(mp_dec_min)

        evidence_ok = bool(hp_up) or bool(mp_down) or bool(cd_marker) or bool(cd_delta) or bool(fb_visible) or bool(fb_delta)

        after = cand
        after_hp = float(cand_hp)
        after_mp = float(cand_mp)
        cooldown_after = bool(cd_marker)
        delta_ok = bool(cd_delta)
        feedback = bool(fb_visible) or bool(fb_delta)

        if evidence_ok:
            break
        if int(time.monotonic_ns()) >= int(deadline_ns):
            break

    if after is None:
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
        exc = PreflightFailed('binding_correlation_failed')
        try:
            setattr(exc, 'details', {'event_correlation': event})
        except Exception:
            pass
        raise exc

    hp_up = (float(after_hp) - float(before_hp)) >= float(intent.expected.hp_increase_min)

    # Mana cost is often observable even when HP is full (or delayed).
    try:
        mp_dec_min = float(os.environ.get('FRBOT_HEAL_MP_DECREASE_MIN', '0.005') or '0.005')
    except Exception:
        mp_dec_min = 0.005
    mp_down = (float(before_mp) - float(after_mp)) >= float(mp_dec_min)

    evidence_ok = bool(hp_up) or bool(mp_down) or bool(cooldown_after) or bool(delta_ok) or bool(feedback)

    ctx.healing.last.hp_percent = float(after_hp)
    ctx.healing.last.mp_percent = float(after_mp)
    ctx.healing.last.source = src  # type: ignore[assignment]
    ctx.healing.last.confidence = 1.0

    if not evidence_ok:
        ctx.healing.attempt_count += 1
        if ctx.healing.attempt_count >= int(ctx.config.max_attempts_per_heal):
            abort_exc = PreflightFailed('heal_unverified')
            try:
                input_method = (os.environ.get('FRBOT_INPUT_METHOD', '') or '').strip().lower() or 'postmessage'
                details: dict[str, object] = {
                    'heal_key': str(intent.key),
                    'input_method': str(input_method),
                    'post_heal_max_wait_ms': int(max_wait_ms),
                    'post_heal_poll_ms': int(poll_ms),
                    'hp_before': float(before_hp),
                    'hp_after': float(after_hp),
                    'mp_before': float(before_mp),
                    'mp_after': float(after_mp),
                    'hp_up': bool(hp_up),
                    'mp_down': bool(mp_down),
                    'cooldown_after': bool(cooldown_after),
                    'cooldown_delta_ok': bool(delta_ok),
                    'feedback': bool(feedback),
                }

                # Best-effort ROI deltas for quicker diagnosis.
                try:
                    from runtime.healing_semantics import _crop_rgb  # type: ignore

                    def _mad(a: bytes, b: bytes) -> float | None:
                        if not a or not b or len(a) != len(b):
                            return None
                        return float(sum(abs(int(x) - int(y)) for x, y in zip(a, b))) / float(len(a))

                    roi_mads: dict[str, float] = {}
                    for roi_name in (str(ctx.config.hp_bar_roi), str(ctx.config.mp_bar_roi), str(ctx.config.heal_cooldown_roi)):
                        roi = ctx.rois.get(roi_name)
                        if roi is None:
                            continue
                        b = _crop_rgb(before, roi)
                        a = _crop_rgb(after, roi)
                        v = _mad(b, a)
                        if v is not None:
                            roi_mads[str(roi_name)] = float(v)
                    if roi_mads:
                        details['roi_mads'] = roi_mads
                except Exception:
                    pass

                setattr(abort_exc, 'details', details)
            except Exception:
                pass

            raise abort_exc
        return False

    ctx.healing.attempt_count = 0
    ctx.healing.attempt_started_ts_ms = 0
    return True
