# looting_basic (PROD_EMERGENCY)

This repository provides an **isolated** gate: `looting_basic`.

- Runs **exactly one** input (loot gesture) and then validates evidence on the AFTER frame.
- No movement, no targeting, no retries.

## Enable

Set:

- `FRBOT_MODE=looting_basic`

Optionally configure:

- `FRBOT_QUICK_LOOT_KEY` (default: `R`)
- `FRBOT_INVENTORY_TEXT_ROI` (default: `inventory_text`)

REAL (prod_emergency) also requires:

- In `prod_emergency`, the certified gesture is forced to `alt_q` (emits `Alt+Q` as the single loot action).
- No ClickXY is required for the certified run.
- If using OBS display capture: `FRBOT_FRAME_COORD_SPACE=screen`

## Required ROIs (REAL, prod_emergency)

The prod_emergency REAL ROI inventory may include this allowlisted superset:

- Baseline required: `minimap`, `battle_list`, `hp_mp`, `target_frame`
- Additional for `looting_basic`: `inventory_text`

## Preflight invariants

`looting_basic` will abort (with a canonical reason) if any invariant is not met:

- `looting_inventory_unreadable`: `inventory_text` ROI missing or not semantically readable.
- `inventory_overlay_missing`: the binary 0xBEEF overlay is not present/coherent in the capture.
- `looting_action_not_configured`: the configured loot gesture does not appear to trigger Quick Loot.
- `looting_window_binding_lost`: HWND binding lost.
- `obs_source_not_found`: missing `FRBOT_OBS_SOURCE_NAME` in `obs_source` mode.

## Evidence rules

After one quick-loot input, it accepts evidence by:

- `inventory_delta`: semantic inventory snapshot delta indicates items increased (e.g. gold ↑) or `capacity_used` ↑.

Chat-only fallback (inventory becomes unreadable AFTER):

- Default: **disabled** (FAIL if inventory is unreadable AFTER).
- When explicitly enabled (see below):
  - `chat_delta_inventory_unreadable`: pixel-only chat semantics proof gated on inventory being unreadable AFTER.

Otherwise it aborts with `looting_no_inventory_delta` (or `looting_inventory_unreadable` if the AFTER inventory snapshot cannot be read).

### Emergency override (OFF by default)

In `prod_emergency` only, you may explicitly allow a chat-evidence fallback **only when the inventory AFTER snapshot is unreadable**.

- Enable: `FRBOT_LOOTING_ALLOW_CHAT_FALLBACK=1`
- Behavior: if chat evidence confirms loot within the allowed latency window and inventory AFTER is unreadable, the gate may PASS with `evidence_kind="chat_delta_inventory_unreadable"`.
- Audit: `tools/audit_emergency.py` will emit warning `looting_chat_fallback_used` when this path is used.

This is intentionally opt-in and is ignored outside `FRBOT_PROFILE=prod_emergency`.

Note (PROD_EMERGENCY): evidence is binary-only. The `inventory_text` ROI is a 2x1 pixel ROI carrying `0xBEEF + u16 gold + u16 cap_used` (no OCR).

## REAL evidence artifacts

On aborts with frame dumping enabled, a BEFORE/AFTER pair is written under `diagnostics/frames/...` for gate `looting_basic`.

## Non-certifying helper: visual ClickXY suggestions

When `FRBOT_LOOTING_BASIC_LOOT_X/Y` is missing, `looting_basic` can dump a **non-certifying** ordered list of nearby ClickXY candidates that look like a corpse tile.

- Input (required to suggest): approximate player position in **frame coords**
  - `FRBOT_LOOTING_PLAYER_X`, `FRBOT_LOOTING_PLAYER_Y`
- Output (dump): `diagnostics/frames_emergency/looting_basic_visual_click_suggestions.json` (or `diagnostics/frames/...` outside prod_emergency)

Tuning (optional):

- `FRBOT_CORPSE_SUGGEST_RADIUS_PX` (default `96`)
- `FRBOT_CORPSE_SUGGEST_TILE_PX` (default `32`)
- `FRBOT_CORPSE_SUGGEST_MAX` (default `12`)

Important:

- This helper never marks SUCCESS and does not relax certification rules.
- In `prod_emergency`, the certified run uses `alt_q` and does not require ClickXY.

## REAL validation tool: Quick Loot effective (Shift+RMB)

Use this tool to confirm that Shift+RMB is actually executing Quick Loot in this client configuration.

- Executes exactly 1 Shift+RMB on the given ClickXY
- Captures BEFORE/AFTER
- Validates at least one evidence:
  - inventory delta (preferred), OR
  - optional loot panel/list ROI change (if configured via `--loot-panel-roi` / `FRBOT_LOOT_PANEL_OPEN_ROI`)
- If no evidence: abort reason = `quick_loot_not_effective`, and it writes `diagnostics/fatal.log` + dumps

Example:

- `python tools/test_quick_loot_real.py --x 1250 --y 650 --coord-space frame`
- `python tools/test_quick_loot_real.py --gesture alt_q`

## Optional: include in PROD_EMERGENCY audits

- `tools/audit_emergency.py` includes `looting_basic` as part of PROD_EMERGENCY readiness.
- `tools/run_real_obs_tests.py` runs the `looting_basic` gate when present in the configured/required gate set.
