from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts.capture import CaptureAdapter
from contracts.errors import PreflightFailed
from contracts.input import InputAdapter
from contracts.runtime import RuntimeContext
from contracts.window import WindowBindingAdapter

from runtime.looting_basic_runner import execute_looting_basic_once


@dataclass(frozen=True, slots=True)
class LootingFullOutcome:
    ok: bool
    actions_sent: int
    successes: int
    stop_reason: str
    attempts: list[dict[str, Any]]
    evidence_kind: str | None = None
    before_ppm: str | None = None
    after_ppm: str | None = None


def execute_looting_full(
    ctx: RuntimeContext,
    *,
    capture: CaptureAdapter,
    input_: InputAdapter,
    binding: WindowBindingAdapter,
    max_actions: int,
    stop_no_delta: int,
    gate: str = 'looting_full',
) -> LootingFullOutcome:
    """Execute a bounded multi-action looting loop.

    Contract:
    - Each action is exactly one input (delegated to execute_looting_basic_once).
    - PASS only if at least one semantic success is observed.
    - Stop when we observe `stop_no_delta` consecutive no-delta attempts after successes.
    """

    max_actions = max(1, int(max_actions))
    stop_no_delta = max(1, int(stop_no_delta))

    start_actions = int(getattr(getattr(ctx, 'looting', object()), 'attempts_used', 0) or 0)

    successes = 0
    no_delta_streak = 0
    attempts: list[dict[str, Any]] = []
    best_before: str | None = None
    best_after: str | None = None
    best_kind: str | None = None

    for i in range(int(max_actions)):
        try:
            out = execute_looting_basic_once(ctx, capture=capture, input_=input_, binding=binding, gate=str(gate))
            attempts.append(
                {
                    'attempt_index': int(i),
                    'ok': bool(out.ok),
                    'evidence_kind': str(out.evidence_kind),
                    'before_ppm': out.before_ppm,
                    'after_ppm': out.after_ppm,
                    'delta_capacity_used': None if out.delta is None else int(out.delta.capacity_used_delta),
                    'delta_slots': {} if out.delta is None else dict(out.delta.slot_deltas),
                }
            )
        except PreflightFailed as exc:
            reason = str(exc)
            attempts.append({'attempt_index': int(i), 'ok': False, 'error': str(reason)})

            if reason in {'looting_no_inventory_delta', 'quick_loot_not_effective'}:
                no_delta_streak += 1
                # Tolerate a small number of no-delta attempts even before the
                # first success. This reduces false negatives from capture/UI
                # latency while keeping the loop bounded.
                if successes <= 0 and int(no_delta_streak) >= int(stop_no_delta):
                    raise PreflightFailed('looting_full_no_evidence') from exc
                if int(no_delta_streak) >= int(stop_no_delta):
                    actions_sent = int(getattr(getattr(ctx, 'looting', object()), 'attempts_used', 0) or 0) - int(start_actions)
                    return LootingFullOutcome(
                        ok=True,
                        actions_sent=int(actions_sent),
                        successes=int(successes),
                        stop_reason='no_delta',
                        attempts=attempts,
                        evidence_kind=best_kind,
                        before_ppm=best_before,
                        after_ppm=best_after,
                    )
                continue

            raise

        if bool(out.ok):
            successes += 1
            no_delta_streak = 0

            # Preserve a certifiable evidence pair from a successful attempt.
            if out.before_ppm and out.after_ppm:
                best_before = str(out.before_ppm)
                best_after = str(out.after_ppm)
                best_kind = str(out.evidence_kind)
            continue

        # Defensive: execute_looting_basic_once should not return ok=False.
        raise PreflightFailed('looting_unverified_action')

    raise PreflightFailed('looting_full_max_actions_reached')
