from __future__ import annotations

import json
import os

from contracts.errors import PreflightFailed
from contracts.runtime import InventorySnapshot, NpcIdentity, RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from diagnostics.fatal import write_fatal
from diagnostics.logger import configure_logger
from runtime.trade_preflight import run as trade_preflight_run
from runtime.trade_runner import execute_trade_tick


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
    out: dict[str, object] = {'slot_counts': dict(inv.slot_counts)}
    if inv.capacity_used is not None:
        out['capacity_used'] = int(inv.capacity_used)
    return out


def _serialize_npc(npc: NpcIdentity | None) -> dict:
    if npc is None:
        return {}
    return {'npc_id': int(npc.npc_id), 'open': bool(npc.open)}


def _load_trade_config_from_env() -> RuntimeConfig:
    backend = (_env_str('FRBOT_TRADE_BACKEND', 'real') or 'real').strip().lower()

    return RuntimeConfig(
        mode=backend,
        tick_hz=_env_float('FRBOT_TICK_HZ', 20.0),
        config_path=_env_str('FRBOT_CONFIG_PATH', ''),

        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,

        trade_max_attempts=_env_int('FRBOT_TRADE_MAX_ATTEMPTS', 3),
        trade_max_ticks=_env_int('FRBOT_TRADE_MAX_TICKS', 20),
        trade_action=_env_str('FRBOT_TRADE_ACTION', 'buy').strip().lower(),
        trade_expected_npc_id=_env_int('FRBOT_TRADE_EXPECTED_NPC_ID', 1),

        trade_inventory_roi=_env_str('FRBOT_TRADE_INVENTORY_ROI', 'trade_inventory'),
        trade_npc_roi=_env_str('FRBOT_TRADE_NPC_ROI', 'trade_npc'),
        trade_action_roi=_env_str('FRBOT_TRADE_ACTION_ROI', 'trade_action'),

        window_hwnd=_env_int('FRBOT_WINDOW_HWND', 0),
        window_title_substring=_env_str('FRBOT_WINDOW_TITLE', ''),
    )


def run_trade_only() -> int:
    """Gate Trade mode (NPC semantics + economic delta evidence).

    Preflight must succeed before runtime.log is created.
    """

    ctx: RuntimeContext | None = None

    try:
        cfg = _load_trade_config_from_env()
        ctx = RuntimeContext(
            config=cfg,
            status=RuntimeStatus(state=RuntimeState.INIT),
            telemetry=RuntimeTelemetry(),
        )

        capture, input_, binding = trade_preflight_run(ctx)

        logger = configure_logger()
        ctx.status.state = RuntimeState.RUNNING

        for tick_index in range(int(cfg.trade_max_ticks)):
            outcome = execute_trade_tick(ctx, capture=capture, input_=input_, binding=binding, tick_index=int(tick_index))
            ev = outcome.evidence

            inv_b = ev.inventory_before
            inv_a = ev.inventory_after

            gold_before = (int(inv_b.slot_counts.get('gold', 0)) if inv_b is not None else None)
            gold_after = (int(inv_a.slot_counts.get('gold', 0)) if inv_a is not None else None)
            items_before = (int(inv_b.slot_counts.get('item', 0)) if inv_b is not None else None)
            items_after = (int(inv_a.slot_counts.get('item', 0)) if inv_a is not None else None)

            payload = {
                'tick_index': int(tick_index),
                'intent_type': str(cfg.trade_action),
                'npc_id': (int(ev.npc.npc_id) if ev.npc is not None else None),
                'gold_before': gold_before,
                'gold_after': gold_after,
                'items_before': items_before,
                'items_after': items_after,
                'inputs_sent': int(ctx.trade.inputs_sent),
            }

            logger.info(json.dumps(payload, separators=(',', ':'), sort_keys=True))

            if outcome.abort_reason is not None:
                raise PreflightFailed(str(outcome.abort_reason))

            if outcome.success:
                logger.info(json.dumps({'status': 'SUCCESS'}, separators=(',', ':'), sort_keys=True))
                return 0

        raise PreflightFailed('trade_unverified_action')

    except PreflightFailed as exc:
        npc = None
        inv_b = None
        inv_a = None
        if ctx is not None:
            npc = getattr(getattr(ctx, 'trade', None), 'last_npc', None)
            inv_b = getattr(getattr(ctx, 'trade', None), 'last_inventory_before', None)
            inv_a = getattr(getattr(ctx, 'trade', None), 'last_inventory_after', None)

        last_inv = inv_a if inv_a is not None else inv_b
        msg = (
            f"reason={exc} "
            f"npc_identity={json.dumps(_serialize_npc(npc), separators=(',', ':'), sort_keys=True)} "
            f"last_inventory_snapshot={json.dumps(_serialize_inventory(last_inv), separators=(',', ':'), sort_keys=True)}"
        )
        write_fatal(msg, exc)
        return 1
    except Exception as exc:
        write_fatal('runtime crashed', exc)
        return 1
