from __future__ import annotations

import os
import sys

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import InventorySnapshot, RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_enabled, dump_pair
from diagnostics.emergency_capture import try_dump_window_frame
from diagnostics.jsonlog import log as log_json
from diagnostics.last_frames import snapshot
from diagnostics.logger import configure_logger

from runtime.looting_basic_preflight import run as looting_basic_preflight_run
from runtime.looting_basic_runner import execute_looting_basic_once


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


def _serialize_inventory(inv: InventorySnapshot | None) -> dict:
    if inv is None:
        return {}
    out: dict[str, object] = {'slot_counts': dict(inv.slot_counts)}
    if inv.capacity_used is not None:
        out['capacity_used'] = int(inv.capacity_used)
    return out


def _load_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_LOOTING_BASIC_BACKEND', 'real') or 'real').strip().lower()

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
        quick_loot_key=_env_str('FRBOT_QUICK_LOOT_KEY', 'R'),
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

        outcome = execute_looting_basic_once(ctx, capture=capture, input_=input_, binding=binding)

        profile = (_env_str('FRBOT_PROFILE', '') or '').strip().lower()
        dump_force = profile == 'prod_emergency'
        if dump_force or dump_enabled():
            before, after = snapshot('looting_basic')
            if before is not None or after is not None:
                dump_pair(gate='looting_basic', before=before, after=after, reason=str(outcome.evidence_kind))

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
                dump_pair(gate='looting_basic', before=before, after=after, reason=str(exc))
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
