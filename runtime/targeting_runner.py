from __future__ import annotations

import os
import time
import numpy as np

from contracts.capture import CaptureAdapter
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import BattleListEntry, Rect, RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from contracts.targeting import IntentTarget
from contracts.window import WindowBindingAdapter
from diagnostics.fatal import write_fatal
from diagnostics.logger import configure_logger
from diagnostics.jsonlog import log as log_json
from diagnostics.last_frames import record_after, record_before
from rules.targeting import select_targeting_intent
from runtime.battle_list_semantics import BattleListObservation, crop_roi_rgb, detect_battle_list
from runtime.battle_list_ocr import check_battle_list_presence
from runtime.env import parse_window_hwnd_env
from runtime.event_correlation import attach_snapshot, new_event, validate
from runtime.targeting_preflight import targeting_preflight
from runtime.pacing import wait_until_ns
from runtime.trace_utils import serialize_for_trace
from runtime.error_policy import should_reraise


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
        window_hwnd=parse_window_hwnd_env('FRBOT_WINDOW_HWND'),
        window_title_substring=env_str('FRBOT_WINDOW_TITLE', ''),
        max_attempts_per_target=int(os.environ.get('FRBOT_MAX_ATTEMPTS_PER_TARGET', '2') or '2'),
        max_time_ms_per_target=int(os.environ.get('FRBOT_MAX_TIME_MS_PER_TARGET', '2500') or '2500'),
    )


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() not in {'', '0', 'false', 'no', 'off'}


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
    except Exception as e:
        if should_reraise():
            raise
        return ''


def _target_frame_visible(rgb: bytes) -> bool:
    # Visible if any non-black pixel exists.
    return any(b != 0 for b in rgb[: min(len(rgb), 300)])


def _target_frame_hp_bar_present(rgb: bytes) -> bool:
    # HP bar present if we detect a strong red-dominant region.
    # Mock uses saturated red; real UI often uses anti-aliased reds/oranges.
    strict_hit = False
    red_dom_hits = 0
    # Tuneable via env for real runs, but safe defaults.
    min_r = int(os.environ.get('FRBOT_TARGET_HP_MIN_R', '140') or '140')
    min_delta = int(os.environ.get('FRBOT_TARGET_HP_MIN_DELTA', '40') or '40')
    min_count = int(os.environ.get('FRBOT_TARGET_HP_MIN_COUNT', '80') or '80')

    # Real clients/themes may render the target HP bar with low saturation or
    # non-red colors. Provide a robust fallback based on luminance contrast.
    try:
        luma_range_min = int(os.environ.get('FRBOT_TARGET_HP_LUMA_RANGE_MIN', '28') or '28')
    except Exception:
        luma_range_min = 28

    for i in range(0, len(rgb) - 2, 3):
        r = int(rgb[i])
        g = int(rgb[i + 1])
        b = int(rgb[i + 2])
        # Strict mock signal.
        if (r > 180) and (g < 60) and (b < 60):
            strict_hit = True
            break
        # Realistic red-dominant signal.
        if r >= min_r and (r - max(g, b)) >= min_delta:
            red_dom_hits += 1
            if red_dom_hits >= min_count:
                return True

    if strict_hit:
        return True

    # Contrast-based fallback (sampled).
    if luma_range_min > 0 and rgb:
        step = max(1, (len(rgb) // 9000))
        lo = 255
        hi = 0
        for i in range(0, len(rgb) - 2, 3 * step):
            r = int(rgb[i])
            g = int(rgb[i + 1])
            b = int(rgb[i + 2])
            y = int((r * 2126 + g * 7152 + b * 722) // 10000)
            if y < lo:
                lo = y
            if y > hi:
                hi = y
            if (hi - lo) >= int(luma_range_min):
                return True

    return False


def execute_intent(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    intent: IntentTarget,
    gate: str = 'targeting',
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

    event = new_event(
        gate=str(gate),
        intent={
            'type': 'targeting_click',
            'target_name': str(getattr(intent, 'target_name', '') or ''),
            'row_index': int(getattr(intent, 'battle_list_row_index', -1)),
        },
    )

    battle_roi = ctx.rois.get(ctx.config.battle_list_roi)
    if battle_roi is None:
        raise PreflightFailed('battle_list_not_detected')

    before_ts_ns = int(time.monotonic_ns())
    attach_snapshot(event, stage='before', ts_ns=before_ts_ns, status=binding.snapshot())
    before = capture.grab()
    record_before(str(gate), before)
    obs = detect_battle_list(before, battle_roi)

    # Fallback: try OCR then structure-based detection
    allow_no_ocr = os.environ.get('FRBOT_BATTLE_LIST_ALLOW_NO_OCR', '').strip().lower() in {'1', 'true', 'yes'}

    if obs is None and not allow_no_ocr:
        # Try real OCR
        try:
            from runtime.battle_list_ocr import detect_monsters_with_ocr
            from PIL import Image

            rgb = crop_roi_rgb(before, battle_roi)
            if rgb:
                w, h = int(battle_roi.width), int(battle_roi.height)
                arr = np.frombuffer(rgb, dtype=np.uint8).reshape(h, w, 3)
                img = Image.fromarray(arr, 'RGB')
                monsters = detect_monsters_with_ocr(img)
                if monsters:
                    entries: list[BattleListEntry] = []
                    for m in monsters:
                        bbox = m['bbox']
                        entry = BattleListEntry(
                            name=m['name'],
                            screen_bbox=Rect(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3]),
                            is_attackable=True,
                            hp_bar_visible=True,
                            highlighted=False,
                            row_index=len(entries),
                        )
                        entries.append(entry)
                    obs = BattleListObservation(
                        container_bbox=Rect(x=0, y=0, width=w, height=h),
                        entries=tuple(entries),
                    )
        except Exception as exc:
            print(f'[targeting_runner] OCR fallback error: {exc}')
            if should_reraise():
                raise

    # If OCR fails, try structure-based detection
    has_structure = False
    if obs is None and not allow_no_ocr:
        # crop_roi_rgb already imported at top
        rgb = crop_roi_rgb(before, battle_roi)
        if rgb:
            w, h = int(battle_roi.width), int(battle_roi.height)
            arr = np.frombuffer(rgb, dtype=np.uint8).reshape(h, w, 3)
            from PIL import Image
            img = Image.fromarray(arr, 'RGB')

            # Get monster rows from structure
            from runtime.battle_list_ocr import detect_battle_list_rows
            rows_result = detect_battle_list_rows(img)

            if rows_result.monster_count > 0:
                has_structure = True
                # Create entries from structure
                entries = []
                for row in rows_result.monsters:
                    entry = BattleListEntry(
                        name=f'row_{row.row_index}',  # Fallback name
                        screen_bbox=Rect(
                            x=int(battle_roi.x + row.name_bbox[0]),
                            y=int(battle_roi.y + row.name_bbox[1]),
                            width=row.name_bbox[2],
                            height=row.name_bbox[3],
                        ),
                        is_attackable=True,
                        hp_bar_visible=row.has_hp_bar,
                        highlighted=False,
                        row_index=row.row_index,
                    )
                    entries.append(entry)
                obs = BattleListObservation(
                    container_bbox=Rect(x=0, y=0, width=w, height=h),
                    entries=tuple(entries),
                )
                print(f'[targeting_runner] Structure detected: {len(entries)} monster rows')

    if obs is None and not allow_no_ocr and not has_structure:
        raise PreflightFailed('battle_list_not_detected')
    if obs is None:
        raise PreflightFailed('battle_list_not_detected')

    clicked_entry = None
    for entry_item in obs.entries:
        if int(entry_item.row_index) == int(intent.battle_list_row_index):
            clicked_entry = entry_item
            break
    if clicked_entry is None:
        raise PreflightFailed('battle_list_not_detected')

    click_x = int(clicked_entry.screen_bbox.x + (clicked_entry.screen_bbox.width // 2))
    click_y = int(clicked_entry.screen_bbox.y + (clicked_entry.screen_bbox.height // 2))

    try:
        binding.assert_bound()
        input_ts_ns = int(time.monotonic_ns())
        attach_snapshot(event, stage='input', ts_ns=input_ts_ns, status=binding.snapshot())
        click_frame = getattr(input_, 'click_frame', None)
        if callable(click_frame):
            click_frame(int(click_x), int(click_y), frame_w=int(before.width), frame_h=int(before.height))
        else:
            input_.click(click_x, click_y)
        ctx.targeting.inputs_sent += 1
    except Exception as exc:
        raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc

    # Real UI may take a moment to reflect selection; OBS screenshots can also lag.
    # Use tick-pacing helper (time.sleep is forbidden by CI guardrails).
    try:
        post_click_ms = int(os.environ.get('FRBOT_POST_CLICK_DELAY_MS', '150') or '150')
    except Exception:
        if should_reraise():
            raise
        post_click_ms = 150
    if post_click_ms > 0:
        wait_until_ns(int(time.monotonic_ns() + (int(post_click_ms) * 1_000_000)))

    after = capture.grab()
    after_ts_ns = int(time.monotonic_ns())
    attach_snapshot(event, stage='after', ts_ns=after_ts_ns, status=binding.snapshot())
    record_after(str(gate), after)
    obs2 = detect_battle_list(after, battle_roi)

    # Fallback for obs2: try OCR then structure-based detection
    if obs2 is None and not allow_no_ocr:
        # Try real OCR
        try:
            from runtime.battle_list_ocr import detect_monsters_with_ocr
            from PIL import Image

            rgb = crop_roi_rgb(after, battle_roi)
            if rgb:
                w, h = int(battle_roi.width), int(battle_roi.height)
                arr = np.frombuffer(rgb, dtype=np.uint8).reshape(h, w, 3)
                img = Image.fromarray(arr, 'RGB')
                monsters = detect_monsters_with_ocr(img)
                if monsters:
                    entries = []
                    for m in monsters:
                        bbox = m['bbox']
                        entry = BattleListEntry(
                            name=m['name'],
                            screen_bbox=Rect(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3]),
                            is_attackable=True,
                            hp_bar_visible=True,
                            highlighted=False,
                            row_index=len(entries),
                        )
                        entries.append(entry)
                    obs2 = BattleListObservation(
                        container_bbox=Rect(x=0, y=0, width=w, height=h),
                        entries=tuple(entries),
                    )
        except Exception as exc:
            print(f'[targeting_runner] OCR fallback error (obs2): {exc}')
            if should_reraise():
                raise

    # If OCR fails, try structure-based detection
    has_structure2 = False
    if obs2 is None and not allow_no_ocr:
        has_structure2 = check_battle_list_presence(after, battle_roi)
        if has_structure2:
            obs2 = BattleListObservation(
                container_bbox=Rect(x=0, y=0, width=int(battle_roi.width), height=int(battle_roi.height)),
                entries=tuple(),
            )

    if obs2 is None and not allow_no_ocr and not has_structure2:
        raise PreflightFailed('battle_list_not_detected')
    if obs2 is None:
        raise PreflightFailed('battle_list_not_detected')

    corr_ok, corr_reason, corr_details = validate(event)
    event['correlation_ok'] = bool(corr_ok)
    event['correlation_reason'] = str(corr_reason)
    if corr_details:
        event['correlation_details'] = dict(corr_details)
    ctx.telemetry.last_event_correlation = serialize_for_trace(event)
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
                try:
                    from runtime.error_policy import should_reraise

                    if should_reraise():
                        raise
                except Exception:
                    pass
        raise corr_exc

    after_row = None
    for entry_item in obs2.entries:
        if int(entry_item.row_index) == int(intent.battle_list_row_index):
            after_row = entry_item
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
            # Prefer a dedicated HP-bar ROI when available.
            hp_roi = ctx.rois.get(getattr(ctx.config, 'target_hp_bar_roi', 'target_hp_bar'))
            hp_rgb = crop_roi_rgb(after, hp_roi) if hp_roi is not None else b''
            tf_hp = _target_frame_hp_bar_present(hp_rgb if hp_rgb else tf_rgb)
            tf_name = _decode_target_frame_name(tf_rgb, int(target_roi.width))
            if intent.expected_evidence.target_frame_visible and not tf_visible:
                success = False
            if intent.expected_evidence.target_hp_bar_present and not tf_hp:
                success = False
            # Only enforce name match when a decodable name is present.
            # (In real mode without mock encoding, tf_name is usually empty.)
            if tf_name:
                if tf_name != intent.target_name:
                    success = False
            else:
                # Optional strictness toggle.
                if _env_bool('FRBOT_REQUIRE_TARGET_FRAME_NAME', False):
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

    # Fallback: try structure-based detection
    if obs is None:
        from runtime.battle_list_ocr import detect_battle_list_rows
        # crop_roi_rgb already imported at top
        from PIL import Image
        import numpy as np
        rgb = crop_roi_rgb(before, battle_roi)
        if rgb:
            w, h = int(battle_roi.width), int(battle_roi.height)
            arr = np.frombuffer(rgb, dtype=np.uint8).reshape(h, w, 3)
            img = Image.fromarray(arr, 'RGB')
            rows_result = detect_battle_list_rows(img)
            if rows_result.monster_count > 0:
                entries: list[BattleListEntry] = []
                for row in rows_result.monsters:
                    entry = BattleListEntry(
                        name=f'row_{row.row_index}',
                        screen_bbox=Rect(
                            x=int(battle_roi.x + row.name_bbox[0]),
                            y=int(battle_roi.y + row.name_bbox[1]),
                            width=row.name_bbox[2],
                            height=row.name_bbox[3],
                        ),
                        is_attackable=True,
                        hp_bar_visible=row.has_hp_bar,
                        highlighted=False,
                        row_index=row.row_index,
                    )
                    entries.append(entry)
                obs = BattleListObservation(
                    container_bbox=Rect(x=0, y=0, width=w, height=h),
                    entries=tuple(entries),
                )
                print(f'[targeting_tick] Structure detected: {len(entries)} monster rows')

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

        tick_hz = max(1e-6, float(ctx.config.tick_hz))
        tick_period_ns = int(1_000_000_000 / float(tick_hz))
        start_ns = time.monotonic_ns()
        next_tick_ns = start_ns

        while True:
            targeting_tick(ctx, capture, input_, binding)

            log_json(
                logger,
                event='tick',
                gate='targeting',
                mode=str(ctx.config.mode),
                tick_count=int(ctx.telemetry.tick_count),
                locked=bool(ctx.targeting.target.locked),
                target=str(ctx.targeting.target.target_name),
                attempts=int(ctx.targeting.attempt_count),
                capture_backend=str(getattr(capture, 'name', type(capture).__name__)),
                capture_source=str(os.environ.get('FRBOT_CAPTURE_SOURCE', '') or 'client').strip().lower(),
                obs_source_name=str(getattr(capture, 'obs_source_name', '') or ''),
                frame_resolution=[int(getattr(capture, 'last_frame_resolution', (0, 0))[0]), int(getattr(capture, 'last_frame_resolution', (0, 0))[1])],
                luma_std=float(getattr(capture, 'last_luma_std', 0.0) or 0.0),
            )

            ctx.telemetry.tick_count += 1
            if (time.monotonic_ns() - start_ns) >= 1_000_000_000:
                return 0

            next_tick_ns += int(tick_period_ns)
            wait_until_ns(int(next_tick_ns))

    except PreflightFailed as exc:
        write_fatal(str(exc), exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(run())
