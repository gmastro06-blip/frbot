from __future__ import annotations

import json
import os

from contracts.errors import ContractViolation, PreflightFailed
from contracts.runtime import DepotSnapshot, InventorySnapshot, RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_enabled, dump_pair
from diagnostics.emergency_capture import try_dump_window_frame
from diagnostics.logger import configure_logger
from diagnostics.jsonlog import log as log_json
from diagnostics.last_frames import snapshot
from runtime.env_bootstrap import load_repo_env
from runtime.deposit_preflight import run as deposit_preflight_run
from runtime.deposit_runner import execute_deposit_tick
from runtime.profile import enforce_feature_allowed


load_repo_env()


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


def _serialize_inventory(inv: InventorySnapshot | None) -> dict[str, object]:
    if inv is None:
        return {}
    out: dict[str, object] = {
        'slot_counts': dict(inv.slot_counts),
    }
    if inv.capacity_used is not None:
        out['capacity_used'] = int(inv.capacity_used)
    return out


def _serialize_depot(d: DepotSnapshot | None) -> dict[str, object]:
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
        enforce_feature_allowed('deposit')
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

            log_json(logger, event='tick', gate='deposit', **payload)

            if outcome.abort_reason is not None:
                raise PreflightFailed(str(outcome.abort_reason))

            if outcome.success:
                log_json(logger, event='success', gate='deposit', status='SUCCESS')
                return 0

        raise PreflightFailed('deposit_timeout')

    except PreflightFailed as exc:
        if dump_enabled():
            before, after = snapshot('deposit')
            if before is not None or after is not None:
                dump_pair(gate='deposit', before=before, after=after, reason=str(exc))
            else:
                try_dump_window_frame(gate='deposit', reason=str(exc))
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
    except ContractViolation as exc:
        if 'Unsupported mode:' in str(exc):
            write_fatal('invalid_mode', exc)
            return 1
        write_fatal('runtime crashed', exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1
