from __future__ import annotations

from importlib import import_module

try:
    _bootstrap_mod = import_module("tools._bootstrap")
except ModuleNotFoundError:
    _bootstrap_mod = import_module("_bootstrap")

_bootstrap_mod.bootstrap_tool_env(__file__)

import json
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path


def _ensure_repo_root_on_syspath() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return repo_root


def _env_str(name: str, default: str = '') -> str:
    raw = os.environ.get(name)
    return (default if raw is None else str(raw)).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(str(raw).strip(), 10) if raw is not None else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(str(raw).strip()) if raw is not None else float(default)
    except Exception:
        return float(default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'', '0', 'false', 'no', 'off'}


def audit_looting_basic_verdict(
    *,
    inventory_delta_ok: bool,
    inventory_unreadable: bool,
    chat_ok: bool,
    allow_chat_fallback: bool,
    used_chat_fallback: bool,
) -> tuple[bool, list[str]]:
    """Compute looting_basic audit verdict from already-derived evidence flags.

    Guardrails:
    - If inventory is readable and delta==0, FAIL always (chat cannot override).
    - Chat-only PASS is allowed only when inventory AFTER is unreadable and the
      explicit emergency fallback is enabled.
    """

    warnings: list[str] = []
    if bool(inventory_delta_ok):
        return True, warnings

    if bool(inventory_unreadable) and bool(allow_chat_fallback) and bool(used_chat_fallback) and bool(chat_ok):
        warnings.append('looting_chat_fallback_used')
        return True, warnings

    return False, warnings


@dataclass(frozen=True, slots=True)
class _Result:
    ok: bool
    verdict: str
    reasons: list[str]
    warnings: list[str]
    features_enabled: list[str]
    features_disabled: list[str]


def _crop_rgb(*, rgb: bytes, img_w: int, img_h: int, x: int, y: int, w: int, h: int) -> bytes:
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        return b''
    if x + w > img_w or y + h > img_h:
        return b''
    row_bytes = img_w * 3
    out = bytearray(w * h * 3)
    dst = 0
    for yy in range(y, y + h):
        src0 = yy * row_bytes + x * 3
        src1 = src0 + w * 3
        out[dst : dst + (w * 3)] = rgb[src0:src1]
        dst += w * 3
    return bytes(out)


def _luma_var(rgb: bytes) -> tuple[float, bool, bool]:
    # Returns (variance, all_zero, all_same).
    if not rgb or (len(rgb) % 3) != 0:
        return 0.0, True, True

    n = len(rgb) // 3
    mean = 0.0
    m2 = 0.0

    all_zero = True
    first = rgb[0:3]
    all_same = True

    for i in range(n):
        r = rgb[i * 3 + 0]
        g = rgb[i * 3 + 1]
        b = rgb[i * 3 + 2]

        if r or g or b:
            all_zero = False
        if all_same and rgb[i * 3 : i * 3 + 3] != first:
            all_same = False

        # integer luma approx, then cast to float
        y = (r * 2126 + g * 7152 + b * 722) / 10000.0
        delta = y - mean
        mean += delta / float(i + 1)
        m2 += delta * (y - mean)

    if n <= 1:
        return 0.0, all_zero, all_same
    return float(m2 / float(n - 1)), all_zero, all_same


def _check_real() -> _Result:
    from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
    from contracts.errors import PreflightFailed
    from diagnostics.frame_dump import dump_frame_ppm
    from runtime.preflight import preflight
    from runtime.profile import is_prod_emergency
    from runtime.env import parse_window_hwnd_env
    from runtime.inventory_semantics import (
        beef_candidate_u16,
        rank_beef_candidates_by_temporal_stability,
        read_inventory_binary,
        scan_beef_candidates_in_frame,
    )

    reasons: list[str] = []
    warnings: list[str] = []

    if not is_prod_emergency():
        return _Result(
            ok=False,
            verdict='NOT_READY',
            reasons=['FRBOT_PROFILE must be prod_emergency'],
            warnings=[],
            features_enabled=[],
            features_disabled=[],
        )

    config_path = Path(_env_str('FRBOT_CONFIG_PATH', ''))
    if not str(config_path):
        reasons.append('FRBOT_CONFIG_PATH missing')
    elif not config_path.exists():
        reasons.append(f'FRBOT_CONFIG_PATH not found: {config_path}')

    required_rois = ['minimap', 'battle_list', 'target_frame', 'hp_mp']
    combat_basic_optional_rois = ['target_hp_bar', 'combat_feedback', 'combat_cooldown']
    inventory_text_roi_name = _env_str('FRBOT_INVENTORY_TEXT_ROI', 'inventory_text') or 'inventory_text'
    # Promotion control: looting_basic is enabled by default unless explicitly disabled.
    # - FRBOT_FEATURE_LOOTING_BASIC_DEFAULT=1 (default)
    # - FRBOT_FEATURE_LOOTING_BASIC=0 to force-disable
    looting_basic_default = _env_bool('FRBOT_FEATURE_LOOTING_BASIC_DEFAULT', True)
    looting_basic_enabled = _env_bool('FRBOT_FEATURE_LOOTING_BASIC', bool(looting_basic_default))

    skip_looting = _env_bool('FRBOT_AUDIT_SKIP_LOOTING', False) or (not bool(looting_basic_enabled))

    out_dir = Path('diagnostics') / 'frames_emergency'
    out_dir.mkdir(parents=True, exist_ok=True)

    if reasons:
        return _Result(ok=False, verdict='NOT_READY', reasons=reasons, warnings=warnings, features_enabled=[], features_disabled=[])

    # Live preflight: verifies binding + capture + minimap marker.
    try:
        cfg = RuntimeConfig(
            mode='real',
            tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
            config_path=str(config_path),
            enable_cavebot=True,
            minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap') or 'minimap',
            window_hwnd=parse_window_hwnd_env('FRBOT_WINDOW_HWND'),
            window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
            player_marker_rgb=_env_str('FRBOT_PLAYER_MARKER_RGB', '255,255,0'),
            player_marker_tol=_env_int('FRBOT_PLAYER_MARKER_TOL', 10),
            player_marker_min_pixels=_env_int('FRBOT_PLAYER_MARKER_MIN_PIXELS', 3),
        )
        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )
        capture, _input, binding = preflight(ctx)
        binding.assert_bound()
        frame = capture.grab()
    except PreflightFailed as exc:
        reasons.append(f'preflight_failed: {exc}')
        try:
            details = getattr(exc, 'details', None)
        except Exception:
            details = None

        fg_hwnd_hex = None
        fg_title = None
        try:
            from adapters.windows import win32 as w32

            fg_hwnd = int(w32.get_foreground_window() or 0)
            fg_hwnd_hex = hex(fg_hwnd) if fg_hwnd > 0 else '0x0'
            fg_title = str(w32.get_window_text(fg_hwnd) or '') if fg_hwnd > 0 else ''
        except Exception:
            # Best-effort diagnostics: win32 helpers may be unavailable or fail.
            pass

        try:
            (out_dir / 'emergency_preflight_failed.json').write_text(
                json.dumps(
                    {
                        'ok': False,
                        'reason': 'preflight_failed',
                        'preflight_reason': str(exc),
                        'details': details,
                        'foreground_hwnd': fg_hwnd_hex,
                        'foreground_title': fg_title,
                        'env': {
                            'FRBOT_PROFILE': _env_str('FRBOT_PROFILE', ''),
                            'FRBOT_MODE': _env_str('FRBOT_MODE', ''),
                            'FRBOT_CAPTURE_SOURCE': _env_str('FRBOT_CAPTURE_SOURCE', ''),
                            'FRBOT_CONFIG_PATH': _env_str('FRBOT_CONFIG_PATH', ''),
                            'FRBOT_WINDOW_HWND': _env_str('FRBOT_WINDOW_HWND', ''),
                            'FRBOT_WINDOW_TITLE': _env_str('FRBOT_WINDOW_TITLE', ''),
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + '\n',
                encoding='utf-8',
            )
        except Exception:
            # Best-effort diagnostics: failure to write debug JSON is non-fatal.
            pass

        try:
            (out_dir / 'emergency_preflight_failed_trace.txt').write_text(
                traceback.format_exc() + '\n',
                encoding='utf-8',
            )
        except Exception:
            # Best-effort diagnostics: failure to write trace is non-fatal.
            pass

        return _Result(ok=False, verdict='NOT_READY', reasons=reasons, warnings=warnings, features_enabled=[], features_disabled=[])

    # Validate required ROI presence + basic non-black evidence.
    enabled: list[str] = []
    disabled: list[str] = ['combat', 'looting', 'deposit', 'trade']

    dump_frame_ppm(frame, out_dir / 'emergency_full.ppm')

    rois = dict(getattr(ctx, 'rois', {}) or {})
    for name in required_rois:
        roi = rois.get(name)
        if roi is None:
            reasons.append(f'missing_required_roi: {name}')
            continue

        crop = _crop_rgb(
            rgb=bytes(frame.rgb),
            img_w=int(frame.width),
            img_h=int(frame.height),
            x=int(getattr(roi, 'x', 0)),
            y=int(getattr(roi, 'y', 0)),
            w=int(getattr(roi, 'width', 0)),
            h=int(getattr(roi, 'height', 0)),
        )
        if not crop:
            reasons.append(f'roi_invalid_or_oob: {name}')
            continue

        var, all_zero, all_same = _luma_var(crop)
        try:
            from contracts.capture import Frame

            dump_frame_ppm(
                Frame(width=int(getattr(roi, 'width')), height=int(getattr(roi, 'height')), monotonic_ts_ns=0, digest_hex='', rgb=crop),
                out_dir / f'emergency_{name}.ppm',
            )
        except Exception:
            # Best-effort diagnostics: ROI dumps are optional.
            pass

        if all_zero or all_same or var < 5.0:
            reasons.append(f'roi_low_contrast_or_black: {name} var={var:.2f} all_zero={all_zero} all_same={all_same}')
            continue

        if name == 'minimap':
            enabled.append('cavebot_basic')
        if name == 'battle_list':
            enabled.append('targeting_basic')
        if name == 'hp_mp':
            enabled.append('healing_basic')

    # combat_basic minimal ROI contract.
    if not ('target_hp_bar' in rois or 'combat_feedback' in rois):
        reasons.append('missing_required_roi: combat_basic_requires_target_hp_bar_or_combat_feedback')

    # Dump combat_basic optional ROIs if present (can be black at rest).
    for name in combat_basic_optional_rois:
        roi = rois.get(name)
        if roi is None:
            continue
        crop = _crop_rgb(
            rgb=bytes(frame.rgb),
            img_w=int(frame.width),
            img_h=int(frame.height),
            x=int(getattr(roi, 'x', 0)),
            y=int(getattr(roi, 'y', 0)),
            w=int(getattr(roi, 'width', 0)),
            h=int(getattr(roi, 'height', 0)),
        )
        if not crop:
            reasons.append(f'roi_invalid_or_oob: {name}')
            continue
        try:
            from contracts.capture import Frame

            dump_frame_ppm(
                Frame(width=int(getattr(roi, 'width')), height=int(getattr(roi, 'height')), monotonic_ts_ns=0, digest_hex='', rgb=crop),
                out_dir / f'emergency_{name}.ppm',
            )
        except Exception:
            # Best-effort diagnostics: ROI dumps are optional.
            pass

    # Strong audit: run combat_basic preflight as a separate certifiable gate.
    try:
        from contracts.runtime import RuntimeConfig as _RC, RuntimeContext as _RCTX, RuntimeState as _RS, RuntimeStatus as _RSTS, RuntimeTelemetry as _RT
        from runtime.combat_basic_preflight import run as combat_basic_preflight

        cfg2 = _RC(
            mode='real',
            tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
            config_path=str(config_path),
            enable_cavebot=False,
            enable_targeting=False,
            enable_healing=False,
            enable_combat=True,
            minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap') or 'minimap',
            window_hwnd=parse_window_hwnd_env('FRBOT_WINDOW_HWND'),
            window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
            target_frame_roi=_env_str('FRBOT_TARGET_FRAME_ROI', 'target_frame') or 'target_frame',
            target_hp_bar_roi=_env_str('FRBOT_TARGET_HP_BAR_ROI', 'target_hp_bar') or 'target_hp_bar',
            combat_cooldown_roi=_env_str('FRBOT_COMBAT_COOLDOWN_ROI', 'combat_cooldown') or 'combat_cooldown',
            combat_feedback_roi=_env_str('FRBOT_COMBAT_FEEDBACK_ROI', 'combat_feedback') or 'combat_feedback',
            attack_key=_env_str('FRBOT_ATTACK_KEY', 'SPACE') or 'SPACE',
            player_marker_rgb=_env_str('FRBOT_PLAYER_MARKER_RGB', '255,255,0'),
            player_marker_tol=_env_int('FRBOT_PLAYER_MARKER_TOL', 10),
            player_marker_min_pixels=_env_int('FRBOT_PLAYER_MARKER_MIN_PIXELS', 3),
        )
        ctx2 = _RCTX(config=cfg2, status=_RSTS(state=_RS.INIT), telemetry=_RT())
        _cap2, _inp2, bind2 = combat_basic_preflight(ctx2)
        bind2.assert_bound()
        enabled.append('combat_basic')
    except PreflightFailed as exc:
        reasons.append(f'combat_basic_preflight_failed: {exc}')
    except Exception as exc:
        reasons.append(f'combat_basic_preflight_crashed: {type(exc).__name__}: {exc}')

    # looting_basic gate is REQUIRED in PROD_EMERGENCY.
    # For combat_basic-only certification runs, allow skipping looting.
    inv_roi = rois.get(str(inventory_text_roi_name))
    if inv_roi is None:
        if skip_looting:
            warnings.append(f'looting_skipped_missing_roi: {inventory_text_roi_name}')
        else:
            reasons.append(f'missing_required_roi: {inventory_text_roi_name}')
    else:
        try:
            # Dump ROI crop for inspection (may be low-contrast; semantic read is the authority).
            crop = _crop_rgb(
                rgb=bytes(frame.rgb),
                img_w=int(frame.width),
                img_h=int(frame.height),
                x=int(getattr(inv_roi, 'x', 0)),
                y=int(getattr(inv_roi, 'y', 0)),
                w=int(getattr(inv_roi, 'width', 0)),
                h=int(getattr(inv_roi, 'height', 0)),
            )
            if crop:
                try:
                    from contracts.capture import Frame

                    dump_frame_ppm(
                        Frame(width=int(getattr(inv_roi, 'width')), height=int(getattr(inv_roi, 'height')), monotonic_ts_ns=0, digest_hex='', rgb=crop),
                        out_dir / f'emergency_{inventory_text_roi_name}.ppm',
                    )
                except Exception:
                    # Best-effort diagnostics: ROI dump is optional.
                    pass
        except Exception:
            # Best-effort diagnostics: crop/dump failures must not block audit.
            pass

        # Always dump binary-only candidate evidence for assisted calibration.
        # This does not affect readiness verdicts; it is evidence-only.
        try:
            default_cap_max = 50000
            cap_max = _env_int('FRBOT_INVENTORY_BINARY_CAP_MAX', default_cap_max)
            cap_max = max(1, min(int(cap_max), 65535))

            candidates_raw = scan_beef_candidates_in_frame(frame, limit=200, cap_max=int(cap_max), gold_max=None)

            after_frames = []
            try:
                for _i in range(5):
                    after_frames.append(capture.grab())
            except Exception:
                after_frames = []
            stable = rank_beef_candidates_by_temporal_stability(
                before=frame,
                after_frames=[af for af in after_frames if af is not None],
                cap_max=int(cap_max),
                gold_max=None,
                top_n=50,
            )

            candidates = [
                {
                    'x': int(c.x),
                    'y': int(c.y),
                    'w': 2,
                    'h': 1,
                    'raw6_hex': str(c.raw6_hex),
                    'u16': beef_candidate_u16(str(c.raw6_hex)),
                }
                for c in candidates_raw
            ]
            (out_dir / 'emergency_inventory_binary_beef_candidates.json').write_text(
                json.dumps(
                    {
                        'frame_name': 'audit_emergency_full',
                        'cap_max': int(cap_max),
                        'count': int(len(candidates)),
                        'candidates': candidates,
                        'stable_top': stable,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + '\n',
                encoding='utf-8',
            )
        except Exception:
            # Best-effort diagnostics: calibration assistance must not block audit.
            pass

        # Readability check at audit time (readiness).
        # Note: looting_basic certification may still pass with chat evidence when inventory is unreadable.
        inv_now = read_inventory_binary(frame, inv_roi)
        if inv_now is None:
            if skip_looting:
                warnings.append('looting_skipped_inventory_unreadable')
            else:
                warnings.append('looting_inventory_unreadable')

        # Evidence-based operational check (gate-specific).
        audit_gate = (_env_str('FRBOT_AUDIT_GATE', 'looting_basic') or 'looting_basic').strip().lower()

        # Runner dumps evidence in FRBOT_REAL_FRAMES_DIR if provided.
        # IMPORTANT: Path('') becomes '.', which would incorrectly point at the repo root.
        raw_evidence_dir = (_env_str('FRBOT_REAL_FRAMES_DIR', '') or '').strip()
        evidence_dir = Path(raw_evidence_dir) if raw_evidence_dir else out_dir

        def _eval_inventory_delta_gate(*, gate: str) -> None:
            g = (gate or '').strip().lower()
            meta_path = evidence_dir / f'{g}_last_result.json'
            if not meta_path.exists():
                reasons.append(f'{g}_missing_evidence_meta')
                return

            try:
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
            except Exception:
                reasons.append(f'{g}_evidence_meta_unreadable')
                return

            before_name = None if not isinstance(meta, dict) else meta.get('before_ppm')
            after_name = None if not isinstance(meta, dict) else meta.get('after_ppm')
            before_path = evidence_dir / str(before_name) if before_name else None
            after_path = evidence_dir / str(after_name) if after_name else None
            if before_path is None or after_path is None or (not before_path.exists()) or (not after_path.exists()):
                reasons.append(f'{g}_missing_evidence_frames')
                return

            try:
                from diagnostics.ppm import read_ppm
                from contracts.capture import Frame
                from runtime.inventory_semantics import read_inventory_pair_binary, diff_inventory, is_loot_success

                b_img = read_ppm(before_path)
                a_img = read_ppm(after_path)
                b_fr = Frame(width=int(b_img.width), height=int(b_img.height), monotonic_ts_ns=0, digest_hex='', rgb=bytes(b_img.rgb))
                a_fr = Frame(width=int(a_img.width), height=int(a_img.height), monotonic_ts_ns=0, digest_hex='', rgb=bytes(a_img.rgb))

                inv_pair = read_inventory_pair_binary(b_fr, a_fr, inv_roi)
                inventory_delta_ok = False
                inventory_unreadable = inv_pair is None
                if inv_pair is not None:
                    inv_b, inv_a = inv_pair
                    d = diff_inventory(inv_b, inv_a)
                    inventory_delta_ok = bool(is_loot_success(d))

                chat_ok = False
                try:
                    chat_ok = bool(meta.get('chat_ok')) if isinstance(meta, dict) else False
                except Exception:
                    chat_ok = False

                used_chat_fallback = False
                try:
                    used_chat_fallback = bool(meta.get('used_chat_fallback')) if isinstance(meta, dict) else False
                except Exception:
                    used_chat_fallback = False

                # Emergency override is valid only in prod_emergency.
                profile_now = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
                allow_chat_fallback = (profile_now == 'prod_emergency') and bool(_env_bool('FRBOT_LOOTING_ALLOW_CHAT_FALLBACK', False))

                ok_gate, gate_warnings = audit_looting_basic_verdict(
                    inventory_delta_ok=bool(inventory_delta_ok),
                    inventory_unreadable=bool(inventory_unreadable),
                    chat_ok=bool(chat_ok),
                    allow_chat_fallback=bool(allow_chat_fallback),
                    used_chat_fallback=bool(used_chat_fallback),
                )
                if ok_gate:
                    warnings.extend([w for w in gate_warnings if w])
                    enabled.append(str(g))
                    return

                reasons.append(f'{g}_not_confirmed')
            except Exception as exc:
                reasons.append(f'{g}_evidence_eval_failed: {type(exc).__name__}: {exc}')

        def _eval_deposit_basic() -> None:
            g = 'deposit_basic'
            meta_path = evidence_dir / f'{g}_last_result.json'
            if not meta_path.exists():
                reasons.append(f'{g}_missing_evidence_meta')
                return
            try:
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
            except Exception:
                reasons.append(f'{g}_evidence_meta_unreadable')
                return

            before_name = None if not isinstance(meta, dict) else meta.get('before_ppm')
            after_name = None if not isinstance(meta, dict) else meta.get('after_ppm')
            before_path = evidence_dir / str(before_name) if before_name else None
            after_path = evidence_dir / str(after_name) if after_name else None
            if before_path is None or after_path is None or (not before_path.exists()) or (not after_path.exists()):
                reasons.append(f'{g}_missing_evidence_frames')
                return

            depot_roi_name = (_env_str('FRBOT_DEPOT_CONTAINER_ROI', 'depot_container') or 'depot_container').strip()
            depot_roi = rois.get(depot_roi_name)
            if depot_roi is None:
                reasons.append('deposit_depot_unreadable')
                return

            try:
                from diagnostics.ppm import read_ppm
                from contracts.capture import Frame
                from runtime.inventory_semantics import read_inventory_pair_binary, diff_inventory, is_deposit_success
                from runtime.depot_semantics import read_depot_container, compute_depot_delta

                b_img = read_ppm(before_path)
                a_img = read_ppm(after_path)
                b_fr = Frame(width=int(b_img.width), height=int(b_img.height), monotonic_ts_ns=0, digest_hex='', rgb=bytes(b_img.rgb))
                a_fr = Frame(width=int(a_img.width), height=int(a_img.height), monotonic_ts_ns=0, digest_hex='', rgb=bytes(a_img.rgb))

                inv_pair = read_inventory_pair_binary(b_fr, a_fr, inv_roi)
                if inv_pair is None:
                    reasons.append('deposit_inventory_unreadable')
                    return
                inv_b, inv_a = inv_pair
                inv_delta = diff_inventory(inv_b, inv_a)

                depot_b = read_depot_container(b_fr, depot_roi)
                depot_a = read_depot_container(a_fr, depot_roi)
                if depot_b is None or depot_a is None:
                    reasons.append('deposit_depot_unreadable')
                    return
                depot_delta = compute_depot_delta(depot_b, depot_a)

                if bool(is_deposit_success(inv_delta)) and int(getattr(depot_delta, 'item_count_delta', 0) or 0) > 0:
                    enabled.append(g)
                else:
                    reasons.append(f'{g}_not_confirmed')
            except Exception as exc:
                reasons.append(f'{g}_evidence_eval_failed: {type(exc).__name__}: {exc}')

        def _eval_trade_basic() -> None:
            g = 'trade_basic'
            meta_path = evidence_dir / f'{g}_last_result.json'
            if not meta_path.exists():
                reasons.append(f'{g}_missing_evidence_meta')
                return
            try:
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
            except Exception:
                reasons.append(f'{g}_evidence_meta_unreadable')
                return

            before_name = None if not isinstance(meta, dict) else meta.get('before_ppm')
            after_name = None if not isinstance(meta, dict) else meta.get('after_ppm')
            before_path = evidence_dir / str(before_name) if before_name else None
            after_path = evidence_dir / str(after_name) if after_name else None
            if before_path is None or after_path is None or (not before_path.exists()) or (not after_path.exists()):
                reasons.append(f'{g}_missing_evidence_frames')
                return

            inv_roi_name = (_env_str('FRBOT_TRADE_INVENTORY_ROI', 'trade_inventory') or 'trade_inventory').strip()
            inv2_roi = rois.get(inv_roi_name)
            if inv2_roi is None:
                reasons.append('trade_inventory_unreadable')
                return

            try:
                from diagnostics.ppm import read_ppm
                from contracts.capture import Frame
                from runtime.trade_semantics import read_trade_inventory, compute_trade_delta, is_trade_success

                b_img = read_ppm(before_path)
                a_img = read_ppm(after_path)
                b_fr = Frame(width=int(b_img.width), height=int(b_img.height), monotonic_ts_ns=0, digest_hex='', rgb=bytes(b_img.rgb))
                a_fr = Frame(width=int(a_img.width), height=int(a_img.height), monotonic_ts_ns=0, digest_hex='', rgb=bytes(a_img.rgb))

                inv_b = read_trade_inventory(b_fr, inv2_roi)
                inv_a = read_trade_inventory(a_fr, inv2_roi)
                if inv_b is None or inv_a is None:
                    reasons.append('trade_inventory_unreadable')
                    return
                d = compute_trade_delta(inv_b, inv_a)

                intent_type = 'buy'
                try:
                    if isinstance(meta, dict):
                        intent_type = str(meta.get('intent_type') or intent_type)
                    else:
                        intent_type = str(intent_type)
                except Exception:
                    intent_type = 'buy'
                intent_type = (intent_type or 'buy').strip().lower() or 'buy'

                if bool(is_trade_success(d, intent_type)):
                    enabled.append(g)
                else:
                    reasons.append(f'{g}_not_confirmed')
            except Exception as exc:
                reasons.append(f'{g}_evidence_eval_failed: {type(exc).__name__}: {exc}')

        if not skip_looting:
            if audit_gate == 'looting_basic':
                _eval_inventory_delta_gate(gate='looting_basic')
            elif audit_gate == 'looting_full':
                _eval_inventory_delta_gate(gate='looting_full')
            elif audit_gate == 'deposit_basic':
                _eval_deposit_basic()
            elif audit_gate == 'trade_basic':
                _eval_trade_basic()
            else:
                reasons.append(f'FRBOT_AUDIT_GATE invalid: {audit_gate}')

    ok = not reasons
    return _Result(
        ok=ok,
        verdict=('READY_FOR_PROD_EMERGENCY' if ok else 'NOT_READY'),
        reasons=reasons,
        warnings=warnings,
        features_enabled=sorted(set(enabled)),
        features_disabled=sorted(set(disabled)),
    )


def _check_mock(repo_root: Path) -> _Result:
    # For CI: require deterministic unit tests.
    import subprocess

    tests = [
        'tests/test_runtime_log_only_after_preflight.py',
        'tests/test_abort_on_unverified_evidence.py',
        'tests/test_one_intent_one_input.py',
        'tests/test_window_binding_lost_abort.py',
        'tests/test_real_mode_never_runs_unbound.py',
    ]

    env = dict(os.environ)
    env.setdefault('FRBOT_MODE', 'mock')

    p = subprocess.run([sys.executable, '-m', 'pytest', '-q', *tests], cwd=str(repo_root), env=env, capture_output=True, text=True, timeout=60)
    if p.returncode == 0:
        return _Result(
            ok=True,
            verdict='READY_FOR_PROD_EMERGENCY',
            reasons=[],
            warnings=[],
            features_enabled=['mock_contracts'],
            features_disabled=['combat', 'looting', 'deposit', 'trade'],
        )

    out = (p.stdout or '') + (p.stderr or '')
    return _Result(
        ok=False,
        verdict='NOT_READY',
        reasons=['mock_audit_tests_failed', out.strip()[:2000]],
        warnings=[],
        features_enabled=[],
        features_disabled=['combat', 'looting', 'deposit', 'trade'],
    )


def main() -> int:
    repo_root = _ensure_repo_root_on_syspath()

    mode = _env_str('FRBOT_MODE', 'real').lower()

    if mode == 'mock':
        res = _check_mock(repo_root)
    elif mode == 'real':
        res = _check_real()
    else:
        res = _Result(ok=False, verdict='NOT_READY', reasons=[f'FRBOT_MODE invalid: {mode}'], warnings=[], features_enabled=[], features_disabled=[])

    report = {
        'ok': bool(res.ok),
        'verdict': str(res.verdict),
        'mode': str(mode),
        'profile': _env_str('FRBOT_PROFILE', ''),
        'features': {'enabled': list(res.features_enabled), 'disabled': list(res.features_disabled)},
        'reasons': list(res.reasons),
        'warnings': list(res.warnings),
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))

    # Required audit trailer for PROD_EMERGENCY REAL certification.
    if str(mode).lower() == 'real':
        print('FINAL DECISION: OPERATIONAL_REAL' if res.ok else 'FINAL DECISION: NOT_OPERATIONAL_REAL')

    if res.ok:
        return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
