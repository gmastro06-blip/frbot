from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.capture import CaptureAdapter
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import DepotSnapshot, InventorySnapshot, RuntimeContext
from contracts.window import WindowBindingAdapter
from runtime.deposit import DepositTickInput, tick
from runtime.depot_semantics import DepotDelta, compute_depot_delta, read_depot_container
from runtime.inventory_semantics import InventoryDelta, compute_inventory_delta, is_deposit_success, read_inventory


@dataclass(frozen=True, slots=True)
class DepositTickEvidence:
    inventory_before: Optional[InventorySnapshot]
    inventory_after: Optional[InventorySnapshot]
    depot_before: Optional[DepotSnapshot]
    depot_after: Optional[DepotSnapshot]
    inventory_delta: Optional[InventoryDelta]
    depot_delta: Optional[DepotDelta]
    status: str


@dataclass(frozen=True, slots=True)
class DepositTickOutcome:
    success: bool
    evidence: DepositTickEvidence
    abort_reason: Optional[str]


def execute_deposit_tick(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    tick_index: int,
) -> DepositTickOutcome:
    """Execute exactly one deposit tick.

    BEFORE capture -> 1 input -> AFTER capture -> semantic evidence validation.
    Any ambiguity -> abort.
    """

    try:
        binding.assert_bound()
    except Exception as exc:
        raise PreflightFailed('deposit_window_binding_lost') from exc

    inv_roi = ctx.rois.get(ctx.config.inventory_text_roi)
    depot_roi = ctx.rois.get(ctx.config.depot_container_roi)
    if inv_roi is None:
        return DepositTickOutcome(
            success=False,
            evidence=DepositTickEvidence(None, None, None, None, None, None, 'deposit_inventory_unreadable'),
            abort_reason='deposit_inventory_unreadable',
        )
    if depot_roi is None:
        return DepositTickOutcome(
            success=False,
            evidence=DepositTickEvidence(None, None, None, None, None, None, 'deposit_unreadable_state'),
            abort_reason='deposit_unreadable_state',
        )

    before = capture.grab()

    inv_before = read_inventory(before, inv_roi)
    if inv_before is None:
        return DepositTickOutcome(
            success=False,
            evidence=DepositTickEvidence(None, None, None, None, None, None, 'deposit_inventory_unreadable'),
            abort_reason='deposit_inventory_unreadable',
        )

    depot_before = read_depot_container(before, depot_roi)
    if depot_before is None:
        return DepositTickOutcome(
            success=False,
            evidence=DepositTickEvidence(inv_before, None, None, None, None, None, 'deposit_unreadable_state'),
            abort_reason='deposit_unreadable_state',
        )

    if not bool(depot_before.open):
        return DepositTickOutcome(
            success=False,
            evidence=DepositTickEvidence(inv_before, None, depot_before, None, None, None, 'deposit_depot_not_open'),
            abort_reason='deposit_depot_not_open',
        )

    ctx.deposit.last_inventory_before = inv_before
    ctx.deposit.last_depot_before = depot_before

    intent, abort = tick(
        DepositTickInput(
            deposit_key=str(ctx.config.deposit_key),
            ticks_used=int(tick_index),
            attempts_used=int(ctx.deposit.attempts_used),
            max_ticks=int(ctx.config.deposit_max_ticks),
            max_attempts=int(ctx.config.deposit_max_attempts),
        )
    )

    if abort is not None:
        return DepositTickOutcome(
            success=False,
            evidence=DepositTickEvidence(inv_before, None, depot_before, None, None, None, str(abort.reason)),
            abort_reason=str(abort.reason),
        )

    if intent is None:
        return DepositTickOutcome(
            success=False,
            evidence=DepositTickEvidence(inv_before, None, depot_before, None, None, None, 'deposit_no_inventory_delta'),
            abort_reason='deposit_no_inventory_delta',
        )

    # Emit exactly one input.
    try:
        binding.assert_bound()
        input_.press_key(str(intent.key))
    except Exception as exc:
        raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc

    ctx.deposit.inputs_sent += 1
    ctx.deposit.attempts_used += 1

    after = capture.grab()

    inv_after = read_inventory(after, inv_roi)
    if inv_after is None:
        return DepositTickOutcome(
            success=False,
            evidence=DepositTickEvidence(inv_before, None, depot_before, None, None, None, 'deposit_inventory_unreadable'),
            abort_reason='deposit_inventory_unreadable',
        )

    depot_after = read_depot_container(after, depot_roi)
    if depot_after is None:
        return DepositTickOutcome(
            success=False,
            evidence=DepositTickEvidence(inv_before, inv_after, depot_before, None, None, None, 'deposit_unreadable_state'),
            abort_reason='deposit_unreadable_state',
        )

    inv_delta = compute_inventory_delta(inv_before, inv_after)
    depot_delta = compute_depot_delta(depot_before, depot_after)

    ctx.deposit.last_inventory_after = inv_after
    ctx.deposit.last_depot_after = depot_after

    inv_success = bool(is_deposit_success(inv_delta))
    depot_success = int(depot_delta.item_count_delta) > 0

    # Partial failure: both indicate movement but magnitudes disagree.
    inv_gold_delta = int(inv_delta.slot_deltas.get('gold', 0))
    depot_count_delta = int(depot_delta.item_count_delta)
    if inv_gold_delta < 0 and depot_count_delta > 0:
        if abs(int(inv_gold_delta)) != int(depot_count_delta):
            return DepositTickOutcome(
                success=False,
                evidence=DepositTickEvidence(inv_before, inv_after, depot_before, depot_after, inv_delta, depot_delta, 'deposit_partial_failure'),
                abort_reason='deposit_partial_failure',
            )

    # If inventory decreased but depot did not increase while depot is open/readable, treat as partial failure.
    if inv_success and not depot_success:
        return DepositTickOutcome(
            success=False,
            evidence=DepositTickEvidence(inv_before, inv_after, depot_before, depot_after, inv_delta, depot_delta, 'deposit_partial_failure'),
            abort_reason='deposit_partial_failure',
        )

    # If depot increased but inventory did not decrease, also treat as partial/ambiguous.
    if depot_success and not inv_success:
        return DepositTickOutcome(
            success=False,
            evidence=DepositTickEvidence(inv_before, inv_after, depot_before, depot_after, inv_delta, depot_delta, 'deposit_partial_failure'),
            abort_reason='deposit_partial_failure',
        )

    if inv_success and depot_success:
        return DepositTickOutcome(
            success=True,
            evidence=DepositTickEvidence(inv_before, inv_after, depot_before, depot_after, inv_delta, depot_delta, 'ok_deposit_confirmed'),
            abort_reason=None,
        )

    # No semantic evidence.
    if ctx.deposit.attempts_used >= int(ctx.config.deposit_max_attempts):
        return DepositTickOutcome(
            success=False,
            evidence=DepositTickEvidence(inv_before, inv_after, depot_before, depot_after, inv_delta, depot_delta, 'deposit_no_inventory_delta'),
            abort_reason='deposit_no_inventory_delta',
        )

    return DepositTickOutcome(
        success=False,
        evidence=DepositTickEvidence(inv_before, inv_after, depot_before, depot_after, inv_delta, depot_delta, 'deposit_no_inventory_delta_retryable'),
        abort_reason=None,
    )
