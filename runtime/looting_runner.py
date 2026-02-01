from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.capture import CaptureAdapter, Frame
from contracts.errors import PreflightFailed
from contracts.evidence import Roi
from contracts.input import InputAdapter
from contracts.runtime import InventorySnapshot, RuntimeContext
from contracts.window import WindowBindingAdapter
from runtime.inventory_semantics import InventoryDelta, diff_inventory, is_loot_success, read_inventory
from runtime.looting_engine import LootingTickInput, select_looting_intent


@dataclass(frozen=True, slots=True)
class LootingTickEvidence:
    inventory_before: Optional[InventorySnapshot]
    inventory_after: Optional[InventorySnapshot]
    delta: Optional[InventoryDelta]
    container_open_before: Optional[bool]
    container_open_after: Optional[bool]
    status: str


@dataclass(frozen=True, slots=True)
class LootingTickOutcome:
    looted: bool
    evidence: LootingTickEvidence
    abort_reason: Optional[str]


def _read_container_open(frame: Frame | None, roi: Roi | None) -> bool | None:
    if frame is None or not getattr(frame, 'rgb', b''):
        return None
    try:
        w = int(getattr(frame, 'width', 0))
        h = int(getattr(frame, 'height', 0))
        if w <= 0 or h <= 0:
            return None
        if roi is None:
            return None
        if int(roi.x) < 0 or int(roi.y) < 0:
            return None
        if (int(roi.x) + int(roi.width)) > w or (int(roi.y) + int(roi.height)) > h:
            return None

        row_stride = w * 3
        out_row_stride = int(roi.width) * 3
        src = frame.rgb
        for row in range(int(roi.height)):
            start = ((int(roi.y) + row) * row_stride) + (int(roi.x) * 3)
            end = start + out_row_stride
            for b in src[start:end]:
                if int(b) != 0:
                    return True
        return False
    except Exception:
        return None


def _roi_center(roi: Roi) -> tuple[int, int]:
    cx = int(roi.x) + (int(roi.width) // 2)
    cy = int(roi.y) + (int(roi.height) // 2)
    return cx, cy


def execute_looting_tick(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    tick_index: int,
) -> LootingTickOutcome:
    """Execute exactly one looting tick.

    Invariant:
    - 1 intent -> 1 input -> 1 AFTER evidence check.

    Success requires semantic inventory delta (no hashes).
    """

    try:
        binding.assert_bound()
    except Exception as exc:
        raise PreflightFailed('looting_window_binding_lost') from exc

    before = capture.grab()

    inv_roi = ctx.rois.get(ctx.config.inventory_text_roi)
    if inv_roi is None:
        return LootingTickOutcome(
            looted=False,
            evidence=LootingTickEvidence(None, None, None, None, None, 'looting_ambiguous_result'),
            abort_reason='looting_ambiguous_result',
        )

    inv_before = read_inventory(before, inv_roi)
    if inv_before is None:
        return LootingTickOutcome(
            looted=False,
            evidence=LootingTickEvidence(None, None, None, None, None, 'looting_inventory_unreadable'),
            abort_reason='looting_inventory_unreadable',
        )

    ctx.looting.last_inventory = inv_before

    mode = str(ctx.looting.mode).strip().lower()
    container_open_before: Optional[bool] = None
    if mode == 'free':
        open_roi = ctx.rois.get(ctx.config.loot_container_open_roi)
        if open_roi is None:
            return LootingTickOutcome(
                looted=False,
                evidence=LootingTickEvidence(inv_before, None, None, None, None, 'looting_ambiguous_result'),
                abort_reason='looting_ambiguous_result',
            )
        container_open_before = _read_container_open(before, open_roi)
        if container_open_before is None:
            return LootingTickOutcome(
                looted=False,
                evidence=LootingTickEvidence(inv_before, None, None, None, None, 'looting_container_state_unknown'),
                abort_reason='looting_container_state_unknown',
            )
        ctx.looting.container_open = bool(container_open_before)

    intent, abort = select_looting_intent(
        LootingTickInput(
            mode=('free' if mode == 'free' else 'premium'),
            container_open=bool(container_open_before) if container_open_before is not None else bool(ctx.looting.container_open),
            quick_loot_key=str(ctx.config.quick_loot_key),
            ticks_used=int(tick_index),
            attempts_used=int(ctx.looting.attempts_used),
            max_ticks=int(ctx.config.looting_max_ticks),
            max_attempts=int(ctx.config.looting_max_attempts_per_corpse),
        )
    )

    if abort is not None:
        return LootingTickOutcome(
            looted=False,
            evidence=LootingTickEvidence(inv_before, None, None, container_open_before, None, str(abort.reason)),
            abort_reason=str(abort.reason),
        )

    if intent is None:
        return LootingTickOutcome(
            looted=False,
            evidence=LootingTickEvidence(inv_before, None, None, container_open_before, None, 'looting_unverified_loot'),
            abort_reason='looting_unverified_loot',
        )

    # Emit exactly one input.
    try:
        binding.assert_bound()
        if intent.kind == 'press_key':
            input_.press_key(str(intent.key or ''))
        else:
            # click
            if mode == 'free':
                if not bool(container_open_before):
                    corpse_roi = ctx.rois.get(ctx.config.loot_corpse_roi)
                    if corpse_roi is None:
                        raise PreflightFailed('looting_ambiguous_result')
                    x, y = _roi_center(corpse_roi)
                    input_.click(x, y)
                else:
                    take_roi = ctx.rois.get(ctx.config.loot_take_roi)
                    if take_roi is None:
                        raise PreflightFailed('looting_ambiguous_result')
                    x, y = _roi_center(take_roi)
                    input_.click(x, y)
            else:
                # Premium mode should never request clicks.
                raise PreflightFailed('looting_invalid_intent')
    except PreflightFailed:
        raise
    except Exception as exc:
        raise PreflightFailed(f'input emit failed: {type(exc).__name__}: {exc}') from exc

    ctx.looting.attempts_used += 1

    after = capture.grab()

    inv_after = read_inventory(after, inv_roi)
    if inv_after is None:
        return LootingTickOutcome(
            looted=False,
            evidence=LootingTickEvidence(inv_before, None, None, container_open_before, None, 'looting_inventory_unreadable'),
            abort_reason='looting_inventory_unreadable',
        )

    ctx.looting.last_inventory = inv_after

    container_open_after: Optional[bool] = None
    if mode == 'free':
        open_roi = ctx.rois.get(ctx.config.loot_container_open_roi)
        container_open_after = _read_container_open(after, open_roi)
        if container_open_after is None:
            return LootingTickOutcome(
                looted=False,
                evidence=LootingTickEvidence(inv_before, inv_after, None, container_open_before, None, 'looting_container_state_unknown'),
                abort_reason='looting_container_state_unknown',
            )
        ctx.looting.container_open = bool(container_open_after)

    delta = diff_inventory(inv_before, inv_after)

    # Free mode: opening the container does not count as loot success.
    if mode == 'free' and not bool(container_open_before):
        if not bool(container_open_after):
            if ctx.looting.attempts_used >= int(ctx.config.looting_max_attempts_per_corpse):
                return LootingTickOutcome(
                    looted=False,
                    evidence=LootingTickEvidence(inv_before, inv_after, delta, container_open_before, container_open_after, 'looting_container_not_open'),
                    abort_reason='looting_container_not_open',
                )
            return LootingTickOutcome(
                looted=False,
                evidence=LootingTickEvidence(inv_before, inv_after, delta, container_open_before, container_open_after, 'looting_container_not_open_retryable'),
                abort_reason=None,
            )

        # Container opened successfully; keep going next tick.
        return LootingTickOutcome(
            looted=False,
            evidence=LootingTickEvidence(inv_before, inv_after, delta, container_open_before, container_open_after, 'ok_container_open'),
            abort_reason=None,
        )

    # Loot success must be an inventory delta.
    if is_loot_success(delta):
        ctx.looting.items_looted += 1
        return LootingTickOutcome(
            looted=True,
            evidence=LootingTickEvidence(inv_before, inv_after, delta, container_open_before, container_open_after, 'ok_looted'),
            abort_reason=None,
        )

    # No semantic delta.
    if ctx.looting.attempts_used >= int(ctx.config.looting_max_attempts_per_corpse):
        return LootingTickOutcome(
            looted=False,
            evidence=LootingTickEvidence(inv_before, inv_after, delta, container_open_before, container_open_after, 'looting_unverified_loot'),
            abort_reason='looting_unverified_loot',
        )

    return LootingTickOutcome(
        looted=False,
        evidence=LootingTickEvidence(inv_before, inv_after, delta, container_open_before, container_open_after, 'looting_unverified_loot_retryable'),
        abort_reason=None,
    )
