# combat_basic (PROD_EMERGENCY)

This repository provides an **isolated** gate: `combat_basic`.

- Runs **exactly one** input and then validates evidence on the AFTER frame.
- No movement, no targeting, no retries.

## Enable

Set:

- `FRBOT_MODE=combat_basic`

## Required ROIs (REAL, prod_emergency)

The prod_emergency REAL ROI inventory may include this allowlisted superset:

- Baseline required: `minimap`, `battle_list`, `hp_mp`, `target_frame`
- Additional for `combat_basic` (at least one): `target_hp_bar`, `combat_feedback`

Example `FRBOT_CONFIG_PATH` JSON:

```json
{
  "frame": {"width": 1920, "height": 1080},
  "rois": {
    "minimap": {"x": 0, "y": 0, "width": 10, "height": 10},
    "battle_list": {"x": 0, "y": 0, "width": 10, "height": 10},
    "hp_mp": {"x": 0, "y": 0, "width": 10, "height": 10},
    "target_frame": {"x": 0, "y": 0, "width": 10, "height": 10},

    "target_hp_bar": {"x": 0, "y": 0, "width": 10, "height": 10},
    "combat_feedback": {"x": 0, "y": 0, "width": 10, "height": 10}
  }
}
```

## Preflight invariants

`combat_basic` will abort (with a canonical reason) if any invariant is not met:

- `combat_invalid_state`: missing required ROIs or unreadable target HP (when configured).
- `combat_window_binding_lost`: HWND binding lost.

## Evidence rules

After one attack input, it accepts evidence only by:

- `locked_after`: target frame indicates a semantic lock in the AFTER frame.

Otherwise it aborts with `combat_unverified_action`.

## REAL evidence artifacts

On aborts with frame dumping enabled, a BEFORE/AFTER pair is written under `diagnostics/frames/...` for gate `combat_basic`.

## Optional: include in PROD_EMERGENCY audits

- `tools/audit_emergency.py` includes `combat_basic` as part of PROD_EMERGENCY readiness.
- `tools/run_real_obs_tests.py` runs the `combat_basic` gate when present in the configured/required gate set.
