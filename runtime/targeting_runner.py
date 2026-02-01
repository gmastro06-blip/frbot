from __future__ import annotations

import os
import time

from contracts.capture import CaptureAdapter
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from contracts.targeting import IntentTarget
from contracts.window import WindowBindingAdapter
from diagnostics.fatal import write_fatal
from diagnostics.logger import configure_logger
from rules.targeting import select_targeting_intent
from runtime.battle_list_semantics import crop_roi_rgb, detect_battle_list
from runtime.config_loader import load_rois
from runtime.targeting_preflight import targeting_preflight


def _load_config_from_env() -> RuntimeConfig:
    mode = os.environ.get('FRBOT_MODE', 'real')
    tick_hz_raw = os.environ.get('FRBOT_TICK_HZ', '20')
    config_path = os.environ.get('FRBOT_CONFIG_PATH', '')

    def env_str(name: str, default: str) -> str:
        raw = os.environ.get(name)
        return default if raw is None else raw

    def env_bool(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return raw.strip() not in {'0', 'false', 'no', 'off'}

    return RuntimeConfig(
        mode=mode,
        tick_hz=float(tick_hz_raw),
        config_path=config_path,
        enable_cavebot=env_bool('FRBOT_ENABLE_CAVEBOT', False),
        enable_targeting=env_bool('FRBOT_ENABLE_TARGETING', True),
        battle_list_roi=env_str('FRBOT_BATTLE_LIST_ROI', 'battle_list'),
        target_frame_roi=env_str('FRBOT_TARGET_FRAME_ROI', 'target_frame'),
        window_hwnd=int(os.environ.get('FRBOT_WINDOW_HWND', '0') or '0'),
        window_title_substring=env_str('FRBOT_WINDOW_TITLE', ''),
        max_attempts_per_target=int(os.environ.get('FRBOT_MAX_ATTEMPTS_PER_TARGET', '2') or '2'),
        max_time_ms_per_target=int(os.environ.get('FRBOT_MAX_TIME_MS_PER_TARGET', '2500') or '2500'),
    )


def _decode_target_frame_name(rgb: bytes, width: int) -> str:
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
    # Visible if any non-black pixel exists.
    return any(b != 0 for b in rgb[: min(len(rgb), 300)])


def _target_frame_hp_bar_present(rgb: bytes) -> bool:
    # HP bar present if any bright red pixel exists.
    for i in range(0, len(rgb) - 2, 3):
        r = rgb[i]
        g = rgb[i + 1]
        b = rgb[i + 2]
        if r > 180 and g < 60 and b < 60:
            return True
    return False


def execute_intent(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    intent: IntentTarget,
) -> bool:
    """Execute a single targeting intent with semantic evidence validation.

    Returns True only if target is locked with objective evidence.
    Raises PreflightFailed on binding loss / ambiguity.
    """

    # Anti-loop per target.
    now_ms = int(time.monotonic_ns() // 1_000_000)
    if ctx.targeting.attempt_target_name != intent.target_name:
        ctx.targeting.attempt_target_name = intent.target_name
        ctx.targeting.attempt_count = 0
        ctx.targeting.attempt_started_ts_ms = now_ms

    if ctx.targeting.attempt_count >= int(ctx.config.max_attempts_per_target):
        raise PreflightFailed('targeting_unstable_or_ambiguous')
    if ctx.targeting.attempt_started_ts_ms and (now_ms - ctx.targeting.attempt_started_ts_ms) >= int(ctx.config.max_time_ms_per_target):
        raise PreflightFailed('targeting_unstable_or_ambiguous')

    # Hard gate before sending input.
    try:
        binding.assert_bound()
    except Exception:
        raise PreflightFailed('targeting_window_binding_lost')

    battle_roi = ctx.rois.get(ctx.config.battle_list_roi)
    if battle_roi is None:
        raise PreflightFailed('battle_list_not_detected')

    before = capture.grab()
    obs = detect_battle_list(before, battle_roi)
    if obs is None:
        raise PreflightFailed('battle_list_not_detected')

    clicked_entry = None
    for e in obs.entries:
        if int(e.row_index) == int(intent.battle_list_row_index):
            clicked_entry = e
            break
    if clicked_entry is None:
        raise PreflightFailed('battle_list_not_detected')

    click_x = int(clicked_entry.screen_bbox.x + (clicked_entry.screen_bbox.width // 2))
    click_y = int(clicked_entry.screen_bbox.y + (clicked_entry.screen_bbox.height // 2))

    try:
        binding.assert_bound()
        input_.click(click_x, click_y)
    except Exception as exc:
        raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc

    after = capture.grab()
    obs2 = detect_battle_list(after, battle_roi)
    if obs2 is None:
        raise PreflightFailed('battle_list_not_detected')

    after_row = None
    for e in obs2.entries:
        if int(e.row_index) == int(intent.battle_list_row_index):
            after_row = e
            break

    success = True
    if intent.expected_evidence.battle_list_row_highlighted:
        if after_row is None or not after_row.highlighted:
            success = False
    if after_row is None or after_row.name != intent.target_name:
        success = False

    if intent.expected_evidence.target_frame_visible or intent.expected_evidence.target_hp_bar_present:
        target_roi = ctx.rois.get(ctx.config.target_frame_roi)
        if target_roi is None:
            raise PreflightFailed('targeting_unstable_or_ambiguous')
        tf_rgb = crop_roi_rgb(after, target_roi)
        if not tf_rgb:
            success = False
        else:
            tf_visible = _target_frame_visible(tf_rgb)
            tf_hp = _target_frame_hp_bar_present(tf_rgb)
            tf_name = _decode_target_frame_name(tf_rgb, int(target_roi.width))
            if intent.expected_evidence.target_frame_visible and not tf_visible:
                success = False
            if intent.expected_evidence.target_hp_bar_present and not tf_hp:
                success = False
            if tf_name != intent.target_name:
                success = False

    if not success:
        ctx.targeting.attempt_count += 1
        if ctx.targeting.attempt_count >= int(ctx.config.max_attempts_per_target):
            raise PreflightFailed('targeting_unstable_or_ambiguous')
        return False

    ctx.targeting.target.target_id = f'battle_list:{intent.battle_list_row_index}:{intent.target_name}'
    ctx.targeting.target.target_name = intent.target_name
    ctx.targeting.target.target_position = None
    ctx.targeting.target.source = 'battle_list'
    ctx.targeting.target.confidence = 1.0
    ctx.targeting.target.locked = True

    ctx.targeting.attempt_count = 0
    ctx.targeting.attempt_started_ts_ms = 0
    return True


def targeting_tick(ctx: RuntimeContext, capture: CaptureAdapter, input_: InputAdapter, binding: WindowBindingAdapter) -> None:
    if not ctx.config.enable_targeting:
        return

    try:
        binding.assert_bound()
    except Exception:
        raise PreflightFailed('targeting_window_binding_lost')

    before = capture.grab()

    battle_roi = ctx.rois.get(ctx.config.battle_list_roi)
    if battle_roi is None:
        raise PreflightFailed('battle_list_not_detected')
    if (battle_roi.x + battle_roi.width) > before.width or (battle_roi.y + battle_roi.height) > before.height:
        raise PreflightFailed('targeting_window_binding_lost')

    obs = detect_battle_list(before, battle_roi)
    if obs is None:
        raise PreflightFailed('battle_list_not_detected')

    res = select_targeting_intent(ctx.targeting.target, obs.entries)
    if res.abort_reason is not None:
        raise PreflightFailed(res.abort_reason)
    if res.intent is None:
        return

    execute_intent(ctx, capture=capture, input_=input_, binding=binding, intent=res.intent)


def run() -> int:
    try:
        cfg = _load_config_from_env()
        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        capture, input_, binding = targeting_preflight(ctx)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        tick_period = 1.0 / ctx.config.tick_hz
        start_ns = time.monotonic_ns()

        while True:
            targeting_tick(ctx, capture, input_, binding)

            logger.info(
                'mode=%s tick_count=%d locked=%s target=%s attempts=%d',
                ctx.config.mode,
                ctx.telemetry.tick_count,
                ctx.targeting.target.locked,
                ctx.targeting.target.target_name,
                ctx.targeting.attempt_count,
            )

            ctx.telemetry.tick_count += 1
            if (time.monotonic_ns() - start_ns) >= 1_000_000_000:
                return 0

            time.sleep(tick_period)

    except PreflightFailed as exc:
        write_fatal(str(exc), exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(run())
