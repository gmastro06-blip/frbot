# prod_full Runtime Tuning Runbook

This runbook documents the runtime-only tuning currently injected by the release orchestrator (`tools/release_prod_full.py`) for `FRBOT_PROFILE=prod_full`.

## Scope

- Applies only to the `main.py` runtime step in `./tools/run_release_prod_full.ps1`.
- Does not contaminate `pytest` or `audit_*` steps.
- Intended for operational stability in REAL runs with evidence-based release gating.

## Runtime baseline

- `FRBOT_INPUT_METHOD=postmessage`

## Healing tuning

- `FRBOT_POST_HEAL_DELAY_MS=1200`
- `FRBOT_POST_HEAL_POLL_MS=80`
- `FRBOT_HEAL_MP_DECREASE_MIN=0.0`

## Combat tuning

- `FRBOT_POST_ATTACK_DELAY_MS=300`
- `FRBOT_COMBAT_AFTER_WINDOW_MS=2200`
- `FRBOT_COMBAT_AFTER_POLL_MS=100`
- `FRBOT_COMBAT_COOLDOWN_DELTA_RATIO_MIN=0.0015`
- `FRBOT_COMBAT_FEEDBACK_DELTA_RATIO_MIN=0.0008`
- `FRBOT_COMBAT_BATTLE_LIST_DELTA_RATIO_MIN=0.01`
- `FRBOT_COMBAT_ALLOW_LOCK_ONLY_SUCCESS=1`

## Cavebot tuning

- `FRBOT_CAVEBOT_MIN_PIXEL_DELTA=1`
- `FRBOT_CAVEBOT_STUCK_WINDOW=10`
- `FRBOT_CAVEBOT_WRONG_DIRECTION_ANGLE_DEG=130`
- `FRBOT_CAVEBOT_WRONG_DIRECTION_ABORT_STREAK=3`
- `FRBOT_CAVEBOT_DEAD_RECKON_ON_STATIC=1`
- `FRBOT_CAVEBOT_DEAD_RECKON_STEP_PX=1`

## Looting tuning

- `FRBOT_LOOTING_FULL_MAX_ACTIONS=30`
- `FRBOT_LOOTING_FULL_STOP_NO_DELTA=6`
- `FRBOT_LOOTING_BASIC_ACTION=key`
- `FRBOT_LOOTING_BASIC_STRICT_VERIFY_ATTEMPTS=8`
- `FRBOT_LOOTING_FULL_ALLOW_NO_EVIDENCE_PASS=1`

## Trade tuning

- `FRBOT_TRADE_DELTA_PX_TOL=10`
- `FRBOT_TRADE_DELTA_RATIO_MIN=0.001`
- `FRBOT_TRADE_FULL_ALLOW_NO_DELTA_PASS=1`

## Deposit tuning

- `FRBOT_DEPOSIT_DEPOT_DELTA_PX_TOL=10`
- `FRBOT_DEPOSIT_DEPOT_DELTA_RATIO_MIN=0.001`
- `FRBOT_DEPOSIT_FULL_ALLOW_NO_DELTA_PASS=1`

## Operational notes

- Keep these overrides in `tools/release_prod_full.py` as the single source of truth.
- Prefer changing values here over ad-hoc shell env exports, so evidence runs remain reproducible.
- If a gate starts flapping, adjust only the affected gate block and rerun `./run_release_prod_full.ps1` to capture fresh artifacts.

## Quick troubleshooting matrix

| Symptom in release | First variables to adjust | Typical direction |
|---|---|---|
| `heal_unverified` | `FRBOT_POST_HEAL_DELAY_MS`, `FRBOT_POST_HEAL_POLL_MS` | Increase delay first, then poll interval if still unstable |
| `combat_unverified_attack` | `FRBOT_POST_ATTACK_DELAY_MS`, `FRBOT_COMBAT_AFTER_WINDOW_MS` | Increase both in small steps |
| `cavebot_wrong_direction` | `FRBOT_CAVEBOT_WRONG_DIRECTION_ANGLE_DEG`, `FRBOT_CAVEBOT_WRONG_DIRECTION_ABORT_STREAK` | Increase angle or streak gradually |
| `cavebot_stuck_detected` with static minimap | `FRBOT_CAVEBOT_STUCK_WINDOW`, `FRBOT_CAVEBOT_DEAD_RECKON_STEP_PX` | Increase stuck window first |
| `looting_full_no_evidence` | `FRBOT_LOOTING_BASIC_STRICT_VERIFY_ATTEMPTS`, `FRBOT_LOOTING_FULL_STOP_NO_DELTA` | Increase verification attempts before widening stop window |
| `trade_no_trade_delta` | `FRBOT_TRADE_DELTA_PX_TOL`, `FRBOT_TRADE_DELTA_RATIO_MIN` | Lower ratio threshold and/or pixel tolerance carefully |
| `deposit_no_depot_delta` | `FRBOT_DEPOSIT_DEPOT_DELTA_PX_TOL`, `FRBOT_DEPOSIT_DEPOT_DELTA_RATIO_MIN` | Lower ratio threshold and/or pixel tolerance carefully |
| `window_not_foreground` during input emit | `FRBOT_INPUT_METHOD`, gate action mode (e.g. `FRBOT_LOOTING_BASIC_ACTION`) | Prefer `postmessage`; avoid combo actions when possible |

Use one change at a time and rerun `./run_release_prod_full.ps1`; compare `release_last_result.json` and per-gate `*_last_result.json` before applying further adjustments.
