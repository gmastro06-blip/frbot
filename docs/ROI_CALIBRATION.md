# ROI Calibration (Real Mode)

Real mode requires a JSON config file that defines **ROIs (regions of interest)** used for:

- extracting a minimal `Observation` (hp/mana/booleans)
- verifying each emitted input via ROI digest deltas ("ROI must change")

If ROIs are missing or wrong, the runtime will abort (by contract).

## 1) Generate a starter config

From the repo root:

- With `mss` installed (recommended):
  - `poetry run python scripts/generate_rois.py --out diagnostics/rois.json --layout default --monitor 1`

- Without `mss` (uses 1920x1080 fallback):
  - `poetry run python scripts/generate_rois.py --out diagnostics/rois.json --layout default --screen-width 1920 --screen-height 1080`

This creates `diagnostics/rois.json` containing:

- `rois`: the only part required by the runtime loader
- `meta`: informational fields (safe to keep; ignored by loader)

## 2) Point FRBOT at the config

PowerShell:

- `$env:FRBOT_CONFIG_PATH = "diagnostics/rois.json"`
- `$env:FRBOT_MODE = "real"`
- `poetry run python main.py`

CMD:

- `set FRBOT_CONFIG_PATH=diagnostics\rois.json`
- `set FRBOT_MODE=real`
- `poetry run python main.py`

## 3) Calibrate ROIs (required)

The generated ROIs are only a rough guess. You must change `x/y/width/height` so each ROI reliably covers the intended UI element.

Recommended workflow:

1. Use a screenshot tool that shows pixel coordinates (or any image editor that can display cursor position).
2. Capture a screenshot of the target application in the exact size/position you will run it.
3. Update each ROI in `diagnostics/rois.json`:
   - `hp_bar`: a region that reflects HP changes (ideally mostly red).
   - `mana_bar`: a region that reflects mana changes (ideally mostly blue).
   - `target_indicator`: region that toggles when you have / don’t have a target.
   - `loot_indicator`: region that toggles when loot is available.
   - `minimap`: region that changes when moving.
   - `inventory`, `trade`, `depot`: regions that change when you trigger their hotkeys.

## 4) Evidence sanity checks

The runtime currently verifies effects by digest deltas, so pick ROIs that:

- **change when the intent is executed**
- **do not change too often on their own** (avoid animated areas)

If you use the `fullframe` layout preset, evidence verification is likely to produce false positives/negatives due to unrelated animations.

## 5) What to do when it aborts

- Inspect `diagnostics/fatal.log`.
- Typical causes:
  - unknown ROI name (intent references a ROI that isn't in config)
  - ROI doesn't change after the hotkey (calibration wrong, wrong hotkey, wrong window focus)
  - capture/input not verified (missing permissions/dependencies)
