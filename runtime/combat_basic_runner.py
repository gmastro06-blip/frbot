from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from contracts.capture import CaptureAdapter, Frame
from contracts.evidence import Roi
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import RuntimeContext
from contracts.window import WindowBindingAdapter
from diagnostics.last_frames import record_after, record_before
from diagnostics.frame_dump import dump_enabled
from diagnostics.overlay_dump import dump_click_point_overlay

from runtime.combat_basic_semantics import CombatBasicEvidence, feedback_visible, read_target_hp_percent, target_frame_active
from runtime.error_policy import should_reraise


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None else str(raw)


def _parse_click_point_f(raw: str) -> tuple[float, float] | None:
    s = (raw or '').strip()
    if not s:
        return None
    bits = [b.strip() for b in s.replace(';', ',').split(',')]
    if len(bits) != 2:
        return None
    try:
        return float(bits[0]), float(bits[1])
    except Exception as e:
        if should_reraise():
            raise
        return None


def _parse_click_point_i(raw: str) -> tuple[int, int] | None:
    pt = _parse_click_point_f(raw)
    if pt is None:
        return None
    x, y = pt
    try:
        return int(round(float(x))), int(round(float(y)))
    except Exception as e:
        if should_reraise():
            raise
        return None


def _resolve_click_xy(ctx: RuntimeContext) -> tuple[int, int] | None:
    """Resolve click point in frame coordinates.

    Env:
    - FRBOT_COMBAT_BASIC_CLICK_XY="x,y" absolute in frame pixels
    - FRBOT_COMBAT_BASIC_CLICK_ROI="roi_name" for relative placement (default battle_list)
    - FRBOT_COMBAT_BASIC_CLICK_REL="rx,ry" either:
        - 0..1 => relative ratio within ROI
        - >1  => pixel offset from ROI origin
    """

    abs_xy = _parse_click_point_i(_env_str('FRBOT_COMBAT_BASIC_CLICK_XY', ''))
    if abs_xy is not None:
        return abs_xy

    default_roi = str(getattr(ctx.config, 'battle_list_roi', 'battle_list'))
    roi_name = (_env_str('FRBOT_COMBAT_BASIC_CLICK_ROI', default_roi) or default_roi).strip()
    roi = ctx.rois.get(str(roi_name))
    if roi is None:
        return None

    rel = _env_str('FRBOT_COMBAT_BASIC_CLICK_REL', '').strip()
    rel_xy = _parse_click_point_f(rel)
    if rel_xy is not None:
        rx, ry = rel_xy
        if 0.0 <= float(rx) <= 1.0 and 0.0 <= float(ry) <= 1.0:
            x = int(round(float(roi.x) + (float(roi.width) * float(rx))))
            y = int(round(float(roi.y) + (float(roi.height) * float(ry))))
            return x, y
        # Treat as pixel offsets within the ROI.
        x = int(round(float(roi.x) + float(rx)))
        y = int(round(float(roi.y) + float(ry)))
        return x, y

    # Default placement.
    is_battle_list = str(roi_name).strip().lower() in {'battle_list', 'battlelist'}
    if is_battle_list:
        x = int(round(float(roi.x) + (float(roi.width) * 0.55)))
        y = int(round(float(roi.y) + (float(roi.height) * 0.15)))
        return x, y

    x = int(round(float(roi.x) + (float(roi.width) * 0.50)))
    y = int(round(float(roi.y) + (float(roi.height) * 0.50)))
    return x, y


def _click_at(input_: InputAdapter, ctx: RuntimeContext, *, x: int, y: int, right: bool) -> None:
    fw = getattr(ctx, 'frame_width', None)
    fh = getattr(ctx, 'frame_height', None)
    if fw is not None and fh is not None:
        try:
            fw_i = int(fw)
            fh_i = int(fh)
        except Exception:
            fw_i = 0
            fh_i = 0
        if fw_i > 0 and fh_i > 0:
            if bool(right):
                rcf = getattr(input_, 'right_click_frame', None)
                if callable(rcf):
                    rcf(int(x), int(y), frame_w=int(fw_i), frame_h=int(fh_i))
                    return
            cf = getattr(input_, 'click_frame', None)
            if callable(cf):
                cf(int(x), int(y), frame_w=int(fw_i), frame_h=int(fh_i))
                return

    if bool(right):
        rc = getattr(input_, 'right_click', None)
        if callable(rc):
            rc(int(x), int(y))
            return
    try:
        input_.click(int(x), int(y))
    except Exception as e:
        if should_reraise():
            raise
        raise


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw is not None else int(default)
    except Exception as e:
        if should_reraise():
            raise
        return int(default)


def _feedback_roi_around_click(*, frame: Frame, x: int, y: int, size: int) -> Roi | None:
    try:
        w = int(frame.width)
        h = int(frame.height)
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    s = int(size)
    if s <= 0:
        return None

    half = int(s // 2)
    x0 = int(x) - half
    y0 = int(y) - half
    if x0 < 0:
        x0 = 0
    if y0 < 0:
        y0 = 0

    rw = s
    rh = s
    if x0 + rw > w:
        rw = max(1, w - x0)
    if y0 + rh > h:
        rh = max(1, h - y0)

    return Roi(name='click_feedback', x=int(x0), y=int(y0), width=int(rw), height=int(rh))


@dataclass(frozen=True, slots=True)
class CombatBasicOutcome:
    ok: bool
    evidence: CombatBasicEvidence


def _parse_action_spec(raw: str, *, default_attack_key: str) -> tuple[str, str]:
    """Parse FRBOT_COMBAT_BASIC_ACTION into (action_type, action_value).

    Supported:
      - attack_key[:KEY]
      - battle_list_click
      - monster_right_click
    Back-compat aliases are accepted.
    """

    s = (raw or '').strip()
    if not s:
        return 'attack_key', str(default_attack_key)

    # Allow forms like "attack_key:PageUp".
    if ':' in s:
        t, v = s.split(':', 1)
        action_type = (t or '').strip().lower()
        action_value = (v or '').strip()
    else:
        action_type = s.strip().lower()
        action_value = ''

    aliases = {
        'key': 'attack_key',
        'hotkey': 'attack_key',
        'attack': 'attack_key',
        'attack_key': 'attack_key',
        'battlelist_click': 'battle_list_click',
        'battle_list_click': 'battle_list_click',
        'battlelist_leftclick': 'battle_list_click',
        'battle_list_leftclick': 'battle_list_click',
        'battlelist_rightclick': 'monster_right_click',
        'battle_list_rightclick': 'monster_right_click',
        'battlelist_rclick': 'monster_right_click',
        'battle_list_rclick': 'monster_right_click',
        'monster_right_click': 'monster_right_click',
    }
    action_type = aliases.get(action_type, action_type)

    if action_type == 'attack_key':
        if not action_value:
            action_value = str(default_attack_key)
        return action_type, str(action_value)

    if action_type in {'battle_list_click', 'monster_right_click'}:
        return action_type, ''

    return 'invalid', ''


def execute_combat_basic_once(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
) -> CombatBasicOutcome:
    """Execute exactly one combat_basic intent.

    Contract:
    - No movement, no targeting, no retries.
    - 1 intent -> 1 input -> AFTER -> evidence -> decide.
    """

    before = capture.grab()
    record_before('combat_basic', before)

    hp_roi = ctx.rois.get(ctx.config.target_hp_bar_roi)
    fb_roi = ctx.rois.get(ctx.config.combat_feedback_roi)
    if hp_roi is None and fb_roi is None:
        raise PreflightFailed('combat_invalid_state')

    hp_before = (read_target_hp_percent(frame=before, roi=hp_roi) if hp_roi is not None else None)
    fb_before = (feedback_visible(frame=before, roi=fb_roi) if fb_roi is not None else False)

    tf_roi = ctx.rois.get(str(ctx.config.target_frame_roi))
    locked_before = bool(target_frame_active(frame=before, roi=tf_roi) if tf_roi is not None else False)

    # One input only.
    action_type, action_value = _parse_action_spec(
        _env_str('FRBOT_COMBAT_BASIC_ACTION', ''),
        default_attack_key=str(ctx.config.attack_key),
    )
    if action_type == 'invalid':
        raise PreflightFailed('combat_invalid_state')
    click_xy: tuple[int, int] | None = None

    # Binding is enforced only immediately pre-input (policy).
    try:
        binding.assert_bound()
        snap = binding.snapshot()
        input_.assert_bound(int(getattr(snap, 'hwnd', 0) or 0))
    except Exception as exc:
        raise PreflightFailed('combat_window_binding_lost') from exc

    if action_type == 'attack_key':
        input_.press_key(str(action_value))
    elif action_type == 'battle_list_click':
        xy = _resolve_click_xy(ctx)
        if xy is None:
            raise PreflightFailed('combat_invalid_state')
        x, y = xy
        click_xy = (int(x), int(y))
        if dump_enabled():
            dump_click_point_overlay(frames_dir=Path('diagnostics') / 'frames', frame=before, x=int(x), y=int(y), reason='combat_basic_battle_list_click')
        _click_at(input_, ctx, x=int(x), y=int(y), right=False)
    elif action_type == 'monster_right_click':
        xy = _resolve_click_xy(ctx)
        if xy is None:
            raise PreflightFailed('combat_invalid_state')
        x, y = xy
        click_xy = (int(x), int(y))
        if dump_enabled():
            dump_click_point_overlay(frames_dir=Path('diagnostics') / 'frames', frame=before, x=int(x), y=int(y), reason='combat_basic_monster_right_click')
        _click_at(input_, ctx, x=int(x), y=int(y), right=True)
    else:
        raise PreflightFailed('combat_invalid_state')
    ctx.combat.inputs_sent += 1
    ctx.combat.last_click_xy = click_xy
    ctx.combat.last_action_type = str(action_type)
    ctx.combat.last_action_value = str(action_value)
    # AFTER capture (single sample; no sleeps/retries).
    after = capture.grab()
    record_after('combat_basic', after)

    hp_after = (read_target_hp_percent(frame=after, roi=hp_roi) if hp_roi is not None else None)
    fb_after = (feedback_visible(frame=after, roi=fb_roi) if fb_roi is not None else False)
    locked_after = bool(target_frame_active(frame=after, roi=tf_roi) if tf_roi is not None else False)

    # New contract: SUCCESS only if target lock is semantically proven in AFTER.
    evidence_ok = bool(locked_after)
    evidence_kind = 'none' if not evidence_ok else 'locked_after'

    ev = CombatBasicEvidence(
        hp_before=hp_before,
        hp_after=hp_after,
        evidence_ok=bool(evidence_ok),
        evidence_kind=str(evidence_kind),
        feedback_before=bool(fb_before),
        feedback_after=bool(fb_after),
        locked_before=bool(locked_before),
        locked_after=bool(locked_after),
    )

    if not bool(evidence_ok):
        abort = PreflightFailed('combat_unverified_action')
        setattr(
            abort,
            'details',
            {
                'reason': 'combat_unverified_action',
                'action_type': str(action_type),
                'action_value': str(action_value),
                'click_xy': click_xy,
                'locked_before': bool(locked_before),
                'locked_after': bool(locked_after),
                'hp_before': hp_before,
                'hp_after': hp_after,
                'feedback_before': bool(fb_before),
                'feedback_after': bool(fb_after),
            },
        )
        raise abort

    return CombatBasicOutcome(ok=True, evidence=ev)
