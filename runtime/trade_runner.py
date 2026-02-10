from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from contracts.capture import CaptureAdapter
from contracts.capture import Frame
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import InventorySnapshot, NpcIdentity, RuntimeContext, TradeTelemetry
from contracts.window import WindowBindingAdapter
from diagnostics.last_frames import record_after, record_before
from runtime.battle_list_semantics import crop_roi_rgb
from runtime.event_correlation import attach_snapshot, new_event, validate
from runtime.trade import select_trade_intent
from runtime.trade_semantics import TradeDelta, compute_trade_delta, detect_npc_window, is_trade_success, read_trade_inventory


def _frames_dir_for_trade_evidence() -> Path:
    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    if profile in {'prod_full', 'prod_emergency'}:
        return Path('diagnostics') / 'frames_full'
    return Path('diagnostics') / 'frames'


def _try_dump_click_overlay(*, reason: str, frame: Frame | None, x: int | None, y: int | None) -> None:
    if frame is None or x is None or y is None:
        return
    try:
        from diagnostics.frame_dump import dump_enabled
        from diagnostics.overlay_dump import dump_click_point_overlay

        profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
        dump_force = profile in {'prod_full', 'prod_emergency'}
        if not dump_force and not dump_enabled():
            return

        dump_click_point_overlay(
            frames_dir=_frames_dir_for_trade_evidence(),
            frame=frame,
            x=int(x),
            y=int(y),
            reason=str(reason),
        )
    except Exception:
        return


def _changed_ratio(before_rgb: bytes, after_rgb: bytes, *, px_tol: int) -> float:
    if not before_rgb or not after_rgb or len(before_rgb) != len(after_rgb):
        return 0.0
    t = int(px_tol)
    changed = 0
    npx = max(1, len(before_rgb) // 3)
    for i in range(0, len(before_rgb), 3):
        if (
            abs(int(before_rgb[i + 0]) - int(after_rgb[i + 0])) > t
            or abs(int(before_rgb[i + 1]) - int(after_rgb[i + 1])) > t
            or abs(int(before_rgb[i + 2]) - int(after_rgb[i + 2])) > t
        ):
            changed += 1
    return float(changed) / float(npx)


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
    gate: str = 'trade',
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

    gate_name = (str(gate or 'trade') or 'trade').strip().lower()

    event = new_event(
        gate=str(gate_name),
        intent={
            'type': 'trade_action',
            'action': str(getattr(ctx.config, 'trade_action', '') or ''),
        },
    )

    before_ts_ns = int(time.monotonic_ns())
    attach_snapshot(event, stage='before', ts_ns=before_ts_ns, status=binding.snapshot())

    profile = (os.environ.get('FRBOT_PROFILE', '') or '').strip().lower()
    pixel_fallback_ok = profile == 'prod_full'

    before = capture.grab()
    record_before(gate_name, before)

    confirm_rgb_before = b''
    if pixel_fallback_ok and inv_roi is not None:
        confirm_rgb_before = crop_roi_rgb(before, inv_roi)

    npc = None
    inv_before = None
    if not pixel_fallback_ok:
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

    try:
        binding.assert_bound()

        input_ts_ns = int(time.monotonic_ns())
        attach_snapshot(event, stage='input', ts_ns=input_ts_ns, status=binding.snapshot())

        click_cursor = (os.environ.get('FRBOT_TRADE_CLICK_CURSOR', '') or '').strip().lower() in {'1', 'true', 'yes', 'y'}
        if pixel_fallback_ok and click_cursor and hasattr(input_, 'click_cursor'):
            getattr(input_, 'click_cursor')()
        else:
            # Emit exactly one input: click the configured action ROI.
            cx = int(action_roi.x) + (int(action_roi.width) // 2)
            cy = int(action_roi.y) + (int(action_roi.height) // 2)
            try:
                event['intent']['click'] = {'x': int(cx), 'y': int(cy)}
            except Exception:
                pass
            _try_dump_click_overlay(reason='trade_action_click', frame=before, x=int(cx), y=int(cy))
            input_.click(cx, cy)
    except Exception as exc:
        raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc

    ctx.trade.inputs_sent += 1

    after = capture.grab()
    after_ts_ns = int(time.monotonic_ns())
    attach_snapshot(event, stage='after', ts_ns=after_ts_ns, status=binding.snapshot())
    record_after(gate_name, after)

    corr_ok, corr_reason, corr_details = validate(event)
    event['correlation_ok'] = bool(corr_ok)
    event['correlation_reason'] = str(corr_reason)
    if corr_details:
        event['correlation_details'] = dict(corr_details)
    ctx.telemetry.last_event_correlation = dict(event)
    if not corr_ok:
        corr_exc = PreflightFailed('binding_correlation_failed')
        try:
            setattr(corr_exc, 'details', {'event_correlation': event})
        except Exception:
            pass
        raise corr_exc

    if pixel_fallback_ok:
        confirm_rgb_after = b''
        if inv_roi is not None:
            confirm_rgb_after = crop_roi_rgb(after, inv_roi)

        try:
            px_tol = int(os.environ.get('FRBOT_TRADE_DELTA_PX_TOL', '15') or '15')
            ratio_thr = float(os.environ.get('FRBOT_TRADE_DELTA_RATIO_MIN', '0.01') or '0.01')
        except Exception:
            px_tol = 15
            ratio_thr = 0.01

        ratio = _changed_ratio(confirm_rgb_before, confirm_rgb_after, px_tol=int(px_tol))
        if float(ratio) >= float(ratio_thr):
            return TradeTickOutcome(
                success=True,
                evidence=TradeTickEvidence(None, None, None, None, 'ok_trade_confirmed_pixel_delta'),
                abort_reason=None,
            )
        return TradeTickOutcome(
            success=False,
            evidence=TradeTickEvidence(None, None, None, None, 'trade_no_trade_delta'),
            abort_reason='trade_no_trade_delta',
        )

    inv_after = read_trade_inventory(after, inv_roi)
    if inv_after is None:
        # Incomplete evidence after the single input => immediate abort.
        return TradeTickOutcome(
            success=False,
            evidence=TradeTickEvidence(npc, inv_before, None, None, 'trade_unverified_action'),
            abort_reason='trade_unverified_action',
        )

    if npc is None or inv_before is None:
        return TradeTickOutcome(
            success=False,
            evidence=TradeTickEvidence(npc, inv_before, inv_after, None, 'trade_unverified_action'),
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
