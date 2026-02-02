from __future__ import annotations

import json
import os

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import InventorySnapshot, RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_enabled, dump_pair
from diagnostics.emergency_capture import try_dump_window_frame
from diagnostics.logger import configure_logger
from diagnostics.jsonlog import log as log_json
from diagnostics.last_frames import snapshot
from runtime.looting_preflight import run as looting_preflight_run
from runtime.looting_runner import execute_looting_tick
from runtime.profile import enforce_feature_allowed


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None else raw


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
    return raw.strip() not in {'0', 'false', 'no', 'off'}


def _serialize_inventory(inv: InventorySnapshot | None) -> dict:
    if inv is None:
        return {}
    out: dict[str, object] = {
        'slot_counts': dict(inv.slot_counts),
    }
    if inv.capacity_used is not None:
        out['capacity_used'] = int(inv.capacity_used)
    return out


def _load_looting_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_LOOTING_BACKEND', 'real') or 'real').strip().lower()

    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),

        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,

        looting_mode=('free' if _env_str('FRBOT_LOOTING_MODE', 'premium').strip().lower() == 'free' else 'premium'),
        looting_max_attempts_per_corpse=_env_int('FRBOT_LOOTING_MAX_ATTEMPTS', 3),
        looting_max_ticks=_env_int('FRBOT_LOOTING_MAX_TICKS', 20),
        looting_require_inventory_delta=_env_bool('FRBOT_LOOTING_REQUIRE_INVENTORY_DELTA', True),
        quick_loot_key=_env_str('FRBOT_QUICK_LOOT_KEY', 'R'),

        inventory_text_roi=_env_str('FRBOT_INVENTORY_TEXT_ROI', 'inventory_text'),
        loot_container_open_roi=_env_str('FRBOT_LOOT_CONTAINER_OPEN_ROI', 'loot_container_open'),
        loot_corpse_roi=_env_str('FRBOT_LOOT_CORPSE_ROI', 'loot_corpse'),
        loot_take_roi=_env_str('FRBOT_LOOT_TAKE_ROI', 'loot_take'),

        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
    )


def run_looting_only() -> int:
    """Gate Looting mode (inventory delta semantics).

    Invariants:
    - preflight succeeds before runtime.log is created
    - strong binding before every input (real)
    - 1 intent -> 1 input -> AFTER inventory delta evidence
    - no hashes used as proof
    """

    max_total_ticks = _env_int('FRBOT_LOOTING_MAX_TICKS', 20)

    ctx: RuntimeContext | None = None

    try:
        enforce_feature_allowed('looting')
        cfg = _load_looting_config_from_env()
        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        capture, input_, binding = looting_preflight_run(ctx)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        for tick_index in range(int(max_total_ticks)):
            outcome = execute_looting_tick(ctx, capture=capture, input_=input_, binding=binding, tick_index=int(tick_index))

            ev = outcome.evidence

            payload = {
                'tick_index': int(tick_index),
                'mode': str(ctx.looting.mode),
                'attempts_used': int(ctx.looting.attempts_used),
                'items_looted': int(ctx.looting.items_looted),
                'container_open_before': ev.container_open_before,
                'container_open_after': ev.container_open_after,
                'inventory_before': _serialize_inventory(ev.inventory_before),
                'inventory_after': _serialize_inventory(ev.inventory_after),
                'delta': {
                    'slot_deltas': (dict(ev.delta.slot_deltas) if ev.delta is not None else {}),
                    'capacity_used_delta': (int(ev.delta.capacity_used_delta) if ev.delta is not None else 0),
                },
                'status': str(ev.status),
                'abort_reason': (str(outcome.abort_reason) if outcome.abort_reason is not None else None),
            }

            log_json(logger, event='tick', gate='looting', **payload)

            ctx.telemetry.tick_count += 1

            if outcome.abort_reason is not None:
                raise PreflightFailed(str(outcome.abort_reason))

            if outcome.looted:
                log_json(logger, event='success', gate='looting', status='SUCCESS', items_looted=int(ctx.looting.items_looted))
                return 0

        raise PreflightFailed('looting_stuck')

    except PreflightFailed as exc:
        if dump_enabled():
            before, after = snapshot('looting')
            if before is not None or after is not None:
                dump_pair(gate='looting', before=before, after=after, reason=str(exc))
            else:
                try_dump_window_frame(gate='looting', reason=str(exc))
        inv = None
        if ctx is not None:
            inv = getattr(getattr(ctx, 'looting', None), 'last_inventory', None)

        msg = f"abort_reason={exc} last_inventory={json.dumps(_serialize_inventory(inv), separators=(',', ':'), sort_keys=True)}"
        write_fatal(msg, exc)
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
