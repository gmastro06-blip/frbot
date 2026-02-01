from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.capture import CaptureAdapter
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import InventorySnapshot, NpcIdentity, RuntimeContext, TradeTelemetry
from contracts.window import WindowBindingAdapter
from runtime.trade import select_trade_intent
from runtime.trade_semantics import TradeDelta, compute_trade_delta, detect_npc_window, is_trade_success, read_trade_inventory


@dataclass(frozen=True, slots=True)
class TradeTickEvidence:
    npc: Optional[NpcIdentity]
    inventory_before: Optional[InventorySnapshot]
    inventory_after: Optional[InventorySnapshot]
    delta: Optional[TradeDelta]
    status: str


@dataclass(frozen=True, slots=True)
class TradeTickOutcome:
    success: bool
    evidence: TradeTickEvidence
    abort_reason: Optional[str]


def execute_trade_tick(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    tick_index: int,
) -> TradeTickOutcome:
    """Execute exactly one Trade tick.

    BEFORE capture -> 1 input -> AFTER capture -> semantic delta evidence.
    """

    # No retries: one tick is one attempt. If an input was already sent, abort.
    if int(ctx.trade.inputs_sent) > 0:
        return TradeTickOutcome(
            success=False,
            evidence=TradeTickEvidence(ctx.trade.last_npc, ctx.trade.last_inventory_before, ctx.trade.last_inventory_after, None, 'trade_attempts_exhausted'),
            abort_reason='trade_attempts_exhausted',
        )

    try:
        binding.assert_bound()
    except Exception as exc:
        raise PreflightFailed('trade_window_binding_lost') from exc

    inv_roi = ctx.rois.get(ctx.config.trade_inventory_roi)
    npc_roi = ctx.rois.get(ctx.config.trade_npc_roi)
    action_roi = ctx.rois.get(ctx.config.trade_action_roi)
    if inv_roi is None or npc_roi is None or action_roi is None:
        return TradeTickOutcome(
            success=False,
            evidence=TradeTickEvidence(None, None, None, None, 'trade_unverified_action'),
            abort_reason='trade_unverified_action',
        )

    before = capture.grab()

    npc = detect_npc_window(before, npc_roi)
    if npc is None:
        return TradeTickOutcome(
            success=False,
            evidence=TradeTickEvidence(None, None, None, None, 'trade_npc_not_detected'),
            abort_reason='trade_npc_not_detected',
        )
    if int(npc.npc_id) != int(ctx.config.trade_expected_npc_id):
        return TradeTickOutcome(
            success=False,
            evidence=TradeTickEvidence(npc, None, None, None, 'trade_wrong_npc'),
            abort_reason='trade_wrong_npc',
        )

    inv_before = read_trade_inventory(before, inv_roi)
    if inv_before is None:
        return TradeTickOutcome(
            success=False,
            evidence=TradeTickEvidence(npc, None, None, None, 'trade_unverified_action'),
            abort_reason='trade_unverified_action',
        )

    ctx.trade.last_npc = npc
    ctx.trade.last_inventory_before = inv_before

    intent, abort = select_trade_intent(str(ctx.config.trade_action))
    if abort is not None or intent is None:
        return TradeTickOutcome(
            success=False,
            evidence=TradeTickEvidence(npc, inv_before, None, None, 'trade_unverified_action'),
            abort_reason='trade_unverified_action',
        )

    # Emit exactly one input: click the configured action ROI.
    cx = int(action_roi.x) + (int(action_roi.width) // 2)
    cy = int(action_roi.y) + (int(action_roi.height) // 2)

    try:
        binding.assert_bound()
        input_.click(cx, cy)
    except Exception as exc:
        raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc

    ctx.trade.inputs_sent += 1

    after = capture.grab()

    inv_after = read_trade_inventory(after, inv_roi)
    if inv_after is None:
        # Incomplete evidence after the single input => immediate abort.
        return TradeTickOutcome(
            success=False,
            evidence=TradeTickEvidence(npc, inv_before, None, None, 'trade_unverified_action'),
            abort_reason='trade_unverified_action',
        )

    ctx.trade.last_inventory_after = inv_after

    delta = compute_trade_delta(inv_before, inv_after)

    ctx.trade.last_telemetry = TradeTelemetry(
        npc=npc,
        inventory_before=inv_before,
        inventory_after=inv_after,
        gold_before=int(inv_before.slot_counts.get('gold', 0)),
        gold_after=int(inv_after.slot_counts.get('gold', 0)),
        items_before=int(inv_before.slot_counts.get('item', 0)),
        items_after=int(inv_after.slot_counts.get('item', 0)),
        capacity_before=(None if inv_before.capacity_used is None else int(inv_before.capacity_used)),
        capacity_after=(None if inv_after.capacity_used is None else int(inv_after.capacity_used)),
    )

    if is_trade_success(delta, intent.intent_type):
        return TradeTickOutcome(
            success=True,
            evidence=TradeTickEvidence(npc, inv_before, inv_after, delta, 'ok_trade_confirmed'),
            abort_reason=None,
        )

    # No economic evidence at all is an immediate abort.
    if int(delta.gold_delta) == 0 and int(delta.item_delta) == 0 and int(delta.capacity_used_delta) == 0:
        return TradeTickOutcome(
            success=False,
            evidence=TradeTickEvidence(npc, inv_before, inv_after, delta, 'trade_no_inventory_delta'),
            abort_reason='trade_no_inventory_delta',
        )

    # Any non-success delta (including incomplete BUY/SELL patterns) => immediate abort.
    return TradeTickOutcome(
        success=False,
        evidence=TradeTickEvidence(npc, inv_before, inv_after, delta, 'trade_unverified_action'),
        abort_reason='trade_unverified_action',
    )
