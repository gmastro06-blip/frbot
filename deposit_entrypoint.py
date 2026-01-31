from __future__ import annotations

import json
import os

from contracts.errors import PreflightFailed
from contracts.runtime import DepotSnapshot, InventorySnapshot, RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.logger import configure_logger
from runtime.deposit_preflight import run as deposit_preflight_run
from runtime.deposit_runner import execute_deposit_tick


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None else raw


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
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
    out: dict[str, object] = {
        'slot_counts': dict(inv.slot_counts),
    }
    if inv.capacity_used is not None:
        out['capacity_used'] = int(inv.capacity_used)
    return out


def _serialize_depot(d: DepotSnapshot | None) -> dict:
    if d is None:
        return {}
    return {'item_count': int(d.item_count), 'open': bool(d.open)}


def _load_deposit_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_DEPOSIT_BACKEND', 'real') or 'real').strip().lower()

    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),

        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,

        deposit_max_attempts=_env_int('FRBOT_DEPOSIT_MAX_ATTEMPTS', 3),
        deposit_max_ticks=_env_int('FRBOT_DEPOSIT_MAX_TICKS', 20),
        deposit_key=_env_str('FRBOT_DEPOSIT_KEY', 'D'),

        inventory_text_roi=_env_str('FRBOT_INVENTORY_TEXT_ROI', 'inventory_text'),
        depot_container_roi=_env_str('FRBOT_DEPOT_CONTAINER_ROI', 'depot_container'),

        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
    )


def run_deposit_only() -> int:
    """Gate Deposit mode (container semantics + inventory/depot delta).

    Preflight must succeed before runtime.log is created.
    """

    ctx: RuntimeContext | None = None

    try:
        cfg = _load_deposit_config_from_env()
        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        capture, input_, binding = deposit_preflight_run(ctx)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        for tick_index in range(int(cfg.deposit_max_ticks)):
            outcome = execute_deposit_tick(ctx, capture=capture, input_=input_, binding=binding, tick_index=int(tick_index))
            ev = outcome.evidence

            payload = {
                'tick_index': int(tick_index),
                'intent': 'deposit',
                'inputs_sent': int(ctx.deposit.inputs_sent),
                'attempts_used': int(ctx.deposit.attempts_used),
                'delta_detected': bool(outcome.success),
                'inventory_delta': (dict(ev.inventory_delta.slot_deltas) if ev.inventory_delta is not None else {}),
                'depot_delta': ({'item_count_delta': int(ev.depot_delta.item_count_delta)} if ev.depot_delta is not None else {}),
                'success': bool(outcome.success),
                'abort_reason': (str(outcome.abort_reason) if outcome.abort_reason is not None else None),
                'status': str(ev.status),
            }

            logger.info(json.dumps(payload, separators=(',', ':'), sort_keys=True))

            if outcome.abort_reason is not None:
                raise PreflightFailed(str(outcome.abort_reason))

            if outcome.success:
                logger.info(json.dumps({'status': 'SUCCESS'}, separators=(',', ':'), sort_keys=True))
                return 0

        raise PreflightFailed('deposit_timeout')

    except PreflightFailed as exc:
        inv_b = None
        inv_a = None
        dep_b = None
        dep_a = None
        if ctx is not None:
            inv_b = getattr(getattr(ctx, 'deposit', None), 'last_inventory_before', None)
            inv_a = getattr(getattr(ctx, 'deposit', None), 'last_inventory_after', None)
            dep_b = getattr(getattr(ctx, 'deposit', None), 'last_depot_before', None)
            dep_a = getattr(getattr(ctx, 'deposit', None), 'last_depot_after', None)

        msg = (
            f"abort_reason={exc} "
            f"inventory_before={json.dumps(_serialize_inventory(inv_b), separators=(',', ':'), sort_keys=True)} "
            f"inventory_after={json.dumps(_serialize_inventory(inv_a), separators=(',', ':'), sort_keys=True)} "
            f"depot_before={json.dumps(_serialize_depot(dep_b), separators=(',', ':'), sort_keys=True)} "
            f"depot_after={json.dumps(_serialize_depot(dep_a), separators=(',', ':'), sort_keys=True)}"
        )
        write_fatal(msg, exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1
