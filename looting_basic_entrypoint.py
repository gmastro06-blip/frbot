from __future__ import annotations

import os
import sys
import json
from pathlib import Path

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import InventorySnapshot, RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_enabled, dump_pair
from diagnostics.emergency_capture import try_dump_window_frame
from diagnostics.jsonlog import log as log_json
from diagnostics.last_frames import snapshot
from diagnostics.logger import configure_logger
from runtime.env_bootstrap import load_repo_env

from runtime.looting_basic_preflight import run as looting_basic_preflight_run
from runtime.looting_basic_runner import execute_looting_basic_once


load_repo_env()


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None else str(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if name == 'FRBOT_WINDOW_HWND' and raw is not None and str(raw).strip() != '':
        s = str(raw).strip()
        if s.lower().startswith('0x') and len(s) > 2 and set(s[2:].lower()) == {'x'}:
            return int(default)
        try:
            return int(s, 0)
        except Exception as exc:
            raise PreflightFailed('window_hwnd_invalid') from exc
    try:
        return int(raw) if raw is not None else int(default)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw is not None else float(default)
    except Exception:
        return float(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {'0', 'false', 'no', 'off'}


def _should_try_shift_rmb_fallback(exc: PreflightFailed) -> bool:
    reason = str(exc or '').strip().lower()
    profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
    if reason not in {'quick_loot_not_effective', 'looting_basic_not_confirmed'}:
        return False
    if profile != 'prod_emergency':
        return False
    return bool(_env_bool('FRBOT_LOOTING_BASIC_FALLBACK_SHIFT_RMB', True))


def _ensure_shift_rmb_click_point(ctx: RuntimeContext) -> None:
    if (_env_str('FRBOT_LOOTING_BASIC_LOOT_X', '') or '').strip() and (_env_str('FRBOT_LOOTING_BASIC_LOOT_Y', '') or '').strip():
        return

    corpse_roi_name = (_env_str('FRBOT_LOOT_CORPSE_ROI', 'loot_corpse') or 'loot_corpse').strip() or 'loot_corpse'
    roi = ctx.rois.get(str(corpse_roi_name))
    if roi is None:
        return

    try:
        x = int(getattr(roi, 'x', 0)) + (int(getattr(roi, 'width', 0)) // 2)
        y = int(getattr(roi, 'y', 0)) + (int(getattr(roi, 'height', 0)) // 2)
    except Exception:
        return

    os.environ['FRBOT_LOOTING_BASIC_LOOT_X'] = str(int(x))
    os.environ['FRBOT_LOOTING_BASIC_LOOT_Y'] = str(int(y))


def _serialize_inventory(inv: InventorySnapshot | None) -> dict[str, object]:
    if inv is None:
        return {}
    out: dict[str, object] = {'slot_counts': dict(inv.slot_counts)}
    if inv.capacity_used is not None:
        out['capacity_used'] = int(inv.capacity_used)
    return out


def _frames_dir() -> Path:
    raw = (_env_str('FRBOT_REAL_FRAMES_DIR', '') or '').strip()
    if raw:
        return Path(str(raw))
    profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
    if profile == 'prod_emergency':
        return Path('diagnostics') / 'frames_emergency'
    return Path('diagnostics') / 'frames'


def _try_write_last_result(*, evidence_dir: Path, before_ppm: str | None, after_ppm: str | None, chat_ok: bool) -> None:
    try:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        # If the runner already wrote a richer/authoritative last_result.json,
        # do not overwrite it here. Overwrites can also point meta at failed
        # dump_pair attempts, causing audits to report missing frames.
        out_path = evidence_dir / 'looting_basic_last_result.json'
        if out_path.exists():
            return
        payload: dict[str, object] = {
            'gate': 'looting_basic',
            'before_ppm': before_ppm,
            'after_ppm': after_ppm,
            'chat_ok': bool(chat_ok),
        }
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    except Exception:
        return


def _load_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_LOOTING_BASIC_BACKEND', 'real') or 'real').strip().lower()

    # Accept both legacy and Tibia-specific env names.
    quick_loot_key = _env_str('FRBOT_TIBIA_QUICK_LOOT_KEY', _env_str('FRBOT_QUICK_LOOT_KEY', 'R'))

    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),

        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,

        # loot evidence + input
        inventory_text_roi=_env_str('FRBOT_INVENTORY_TEXT_ROI', 'inventory_text'),
        quick_loot_key=str(quick_loot_key),
        looting_max_attempts_per_corpse=1,
        looting_max_ticks=1,
        looting_require_inventory_delta=True,
        looting_mode='premium',

        minimap_roi=_env_str('FRBOT_MINIMAP_ROI', 'minimap'),

        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
    )


def run_looting_basic_only() -> int:
    """looting_basic feature gate.

    Contract:
    - Enabled by default.
    - BEFORE capture -> exactly 1 input -> AFTER capture.
    - Success requires inventory delta OR (if inventory unreadable) bounded chat delta.
    """

    try:
        cfg = _load_config_from_env()

        # Mock backend is used in CI on Linux; only REAL runs are Windows-only.
        if cfg.mode.strip().lower() == 'real' and sys.platform != 'win32':
            write_fatal('unsupported_platform', details={'platform': str(sys.platform)})
            return 1

        ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

        # Preflight must run before runtime.log.
        capture, input_, binding = looting_basic_preflight_run(ctx)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        try:
            outcome = execute_looting_basic_once(ctx, capture=capture, input_=input_, binding=binding)
        except PreflightFailed as exc:
            if not _should_try_shift_rmb_fallback(exc):
                raise
            prev_allow_non_altq = os.environ.get('FRBOT_LOOTING_BASIC_ALLOW_NON_ALTQ')
            os.environ['FRBOT_LOOTING_BASIC_ACTION'] = 'shift_rmb'
            os.environ['FRBOT_LOOTING_BASIC_ALLOW_NON_ALTQ'] = '1'
            _ensure_shift_rmb_click_point(ctx)
            try:
                outcome = execute_looting_basic_once(ctx, capture=capture, input_=input_, binding=binding)
            finally:
                if prev_allow_non_altq is None:
                    os.environ.pop('FRBOT_LOOTING_BASIC_ALLOW_NON_ALTQ', None)
                else:
                    os.environ['FRBOT_LOOTING_BASIC_ALLOW_NON_ALTQ'] = str(prev_allow_non_altq)

        profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
        dump_force = profile == 'prod_emergency'
        if dump_force or dump_enabled():
            before, after = snapshot('looting_basic')
            if before is not None or after is not None:
                before_ppm, after_ppm = dump_pair(gate='looting_basic', before=before, after=after, reason=str(outcome.evidence_kind))
                _try_write_last_result(
                    evidence_dir=_frames_dir(),
                    before_ppm=before_ppm,
                    after_ppm=after_ppm,
                    chat_ok=bool(outcome.ok and str(outcome.evidence_kind).strip().lower().startswith('chat_delta')),
                )

        log_json(
            logger,
            event='tick',
            gate='looting_basic',
            intent_type='looting_basic',
            inputs_sent=int(getattr(ctx.looting, 'attempts_used', 0)),
            inventory_before=_serialize_inventory(outcome.inventory_before),
            inventory_after=_serialize_inventory(outcome.inventory_after),
            delta={
                'slot_deltas': dict(outcome.delta.slot_deltas) if outcome.delta is not None else {},
                'capacity_used_delta': int(outcome.delta.capacity_used_delta) if outcome.delta is not None else 0,
            },
            evidence_ok=bool(outcome.ok),
            evidence_kind=str(outcome.evidence_kind),
            abort_reason='none',
        )
        log_json(logger, event='success', gate='looting_basic', status='SUCCESS', result=str(outcome.evidence_kind))
        return 0

    except PreflightFailed as exc:
        profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
        dump_force = profile == 'prod_emergency'
        if dump_force or dump_enabled():
            before, after = snapshot('looting_basic')
            if before is not None or after is not None:
                before_ppm, after_ppm = dump_pair(gate='looting_basic', before=before, after=after, reason=str(exc))
                _try_write_last_result(evidence_dir=_frames_dir(), before_ppm=before_ppm, after_ppm=after_ppm, chat_ok=False)
            else:
                try_dump_window_frame(gate='looting_basic', reason=str(exc))
        write_fatal(str(exc), exc)
        return 1
    except ContractViolation as exc:
        if 'Unsupported mode:' in str(exc):
            write_fatal('invalid_mode', exc)
            return 1
        write_fatal('runtime crashed', exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(run_looting_basic_only())
