# frbot (rewrite)

Minimal, contract-driven runtime.

Windows-only (PROD-EMERGENCY).

This repository has been rewritten to enforce:

- No dynamic dict state in runtime.
- No GUI/capture automation as a central dependency.
- Fail-fast startup: abort in <1s if environment/contracts are not satisfied.
- Persistent evidence on failure (diagnostics/fatal.log).

## PROD-EMERGENCY (72h)

Emergency safety profile designed for **production-safe** operation under strict constraints:

- No irreversible actions without evidence (evidence-or-abort).
- No infinite loops (bounded ticks per session).
- Auditable (explicit abort reasons + frame dumps on abort).
- Fail fast (prefer abort over acting wrong).

### Scope (hard cut)

Enabled (only if REAL evidence passes):

- REAL capture (HWND / OBS Projector)
- Targeting (battle list semantics)
- Cavebot BASIC (minimap + marker tracking)
- Healing BASIC (critical single-spell intent)

Hard-disabled in `FRBOT_PROFILE=prod_emergency`:

- Combat loop automatic
- Looting loop automatic
- Deposit
- Trade

Note: the isolated single-intent gates `combat_basic` and `looting_basic` are allowed in PROD-EMERGENCY (evidence-or-abort), even though the full-loop modes above are hard-disabled.

Additionally, these feature modes are not runnable in PROD-EMERGENCY (hard abort with `feature_disabled`):

- `FRBOT_MODE=combat`
- `FRBOT_MODE=looting`
- `FRBOT_MODE=deposit`
- `FRBOT_MODE=trade`

### Profile switch

Set:

- `FRBOT_PROFILE=prod_emergency`

Emergency guardrails applied:

- Tick budget is capped to 300 (`session_tick_budget_exhausted` on exhaustion)
- Any `window_binding_lost` aborts immediately
- Runtime abort attempts to dump a frame to `diagnostics/frames_emergency/`

### Minimal ROI config

`rois_prod.json` is the frozen minimal ROI set for PROD-EMERGENCY:

- `minimap`
- `battle_list`
- `target_frame`
- `hp_mp`

If your capture resolution/layout differs, recalibrate and regenerate a matching runtime ROI config.

### Emergency certification

Mock (CI-friendly):

```powershell
$env:FRBOT_MODE = "mock"
poetry run python tools/audit_emergency.py
```

Real (OBS Projector example):

```powershell
$env:FRBOT_MODE = "real"
$env:FRBOT_PROFILE = "prod_emergency"
$env:FRBOT_CAPTURE_BACKEND = "obs-projector"
$env:FRBOT_CAPTURE_TARGET = "projector"
$env:FRBOT_CONFIG_PATH = "rois_prod.json"
$env:FRBOT_PROJECTOR_WINDOW_TITLE = "Proyector en ventana (Fuente) - Tibia_Fuente"
$env:FRBOT_PROJECTOR_REQUIRE_FOREGROUND = "1"
$env:FRBOT_TRY_ALL_OUTPUTS = "1"
$env:FRBOT_MAX_OUTPUTS = "6"
poetry run python tools/audit_emergency.py
```

One-command launcher (recommended):

```powershell
./scripts/run_prod_emergency.ps1
```

## Repo structure (non-negotiable)

- contracts/  → dataclasses + invariants
- core/       → pure logic (no IO, no GUI, no sleeps)
- adapters/   → untrusted external integration points (capture/input)
- runtime/    → state machine + abort paths
- diagnostics/→ persistent logging

## Running

Default is strict and will abort (no real adapters are provided in this rewrite):

```powershell
poetry run python main.py
```

Terminology (consistent across this document):

- `FRBOT_MODE=mock` → deterministic test mode.
- `FRBOT_MODE=real` → REAL execution path (requires verified adapters/prerequisites).
- `FRBOT_PROFILE=prod_emergency|prod_full` → production safety/certification profile used during REAL execution.
- `FRBOT_MODE=targeting|healing|combat_basic|...` → isolated feature gate entry mode.

Deterministic mock mode (headless, testable):

- PowerShell:

```powershell
$env:FRBOT_MODE = "mock"
poetry run python main.py
```

- CMD:

```bat
set FRBOT_MODE=mock
poetry run python main.py
```

Environment template (recommended for local REAL execution):

```powershell
Copy-Item .env.example .env
```

The template includes validated minimap marker defaults for OBS source capture in this workspace:

- `FRBOT_PLAYER_MARKER_RGB=0,200,0`
- `FRBOT_PLAYER_MARKER_TOL=45`

`.env` autoload behavior:

- Entry points and `tools/*.py` now auto-load local `.env` on startup.
- Only `FRBOT_*` keys are imported from `.env`.
- Existing environment variables already set in the shell are not overwritten.
- Practical precedence is: exported shell value > `.env` value > in-code default.

PowerShell export from `.env` is now optional (only needed if you want to override values per-session).

Mock cavebot waypoints:

- If `FRBOT_BOT_CONFIG_PATH` is not set, mock mode uses a small deterministic waypoint loop (keeps CI/test runs operational).
- If you want to use the legacy waypoint file in this repo: set `FRBOT_BOT_CONFIG_PATH=Waypoints/file.json`.

Mock verification toggles (to force abort paths):

- `FRBOT_MOCK_CAPTURE_OK=0` → abort: capture not verified
- `FRBOT_MOCK_INPUT_OK=0` → abort: input not verified

Logs:

- diagnostics/runtime.log
- diagnostics/fatal.log

## Waypoint/Script Editor (Desktop UI)

This repo includes a standalone desktop UI for editing waypoint/scripts.

Scope:

- UI + typed model + JSON load/save only.
- No third-party automation: action buttons only add waypoints and emit signals/logs for future integration.

Install:

```powershell
pip install PySide6
```

Run:

```powershell
python app.py
```

Artifacts:

- `runtime_ui.log`
- Saved scripts follow the canonical schema in `Waypoints/example_script.json`

### Targeting-only mode

Run the semantic targeting system *only* (Battle List + objective evidence, or abort):

- PowerShell:

```powershell
$env:FRBOT_MODE = "targeting"
$env:FRBOT_TARGETING_BACKEND = "mock"  # or "real"
poetry run python main.py
```

What it does:

- Selects EXACTLY one target from Battle List candidates
- Confirms selection via semantic evidence (row highlight + target frame/name)
- Exits the process with SUCCESS (0) or ABORT (1)

What it does NOT do:

- No attacking
- No movement
- No minimap tracking
- No waypoint/cavebot logic
- No looting

Use cases:

- Validate Battle List ROI + parsing
- Validate HWND binding requirements in REAL execution
- Validate click/input wiring with minimal risk

### Healing-only mode

Run the semantic healing system *only* (HP/MP semantics + cooldown + evidence, or abort):

- PowerShell:

```powershell
$env:FRBOT_MODE = "healing"
$env:FRBOT_HEALING_BACKEND = "mock"  # or "real"
poetry run python main.py
```

What it does:

- Reads HP/MP semantically (bar and/or numeric when available)
- If heal is needed and cooldown is verifiable, emits ONE heal intent
- Confirms heal via evidence (HP up OR cooldown visible OR explicit feedback)
- Exits the process with SUCCESS (0) or ABORT (1)

What it does NOT do:

- No targeting
- No attacking
- No movement

### Combat BASIC (single intent, evidence-or-abort)

`combat_basic` is an isolated PROD-EMERGENCY feature gate that emits **exactly one** combat-related input and then proves success via **semantic target-lock evidence** in the AFTER frame (or aborts).

- Mode: `FRBOT_MODE=combat_basic`
- Evidence (required): target lock proven in AFTER (`evidence_kind="locked_after"`)

Recommended runner (REAL + OBS source identity):

```powershell
./scripts/run_combat_basic_real_obs_source.ps1 \
  -WindowHwnd "0x3094a" \
  -ObsSourceName "Tibia_Fuente" \
  -ConfigPath "./rois_prod_emergency_combat_basic.json" \
  -Action attack_key \
  -AttackKey "AvPag" \
  -AutoScan -ScanCenterXY "700,385" \
  -DumpFrames -PostProcessEvidence
```

Artifacts:

- `diagnostics/frames_emergency/` (before/after + click overlay)
- `diagnostics/roi_overlays/`, `diagnostics/roi_crops/`, `diagnostics/diff_overlays/`
- `diagnostics/runtime.log` (JSONL, includes `action` + `click_xy` for correlation)

More details: `docs/combat_basic_prod_emergency.md`

No cavebot/waypoints.

Use cases:

- Validate target-frame ROI observability/consistency
- Validate HWND binding + safe input with minimal surface area

### Looting BASIC (single intent, evidence-or-abort)

`looting_basic` is an isolated PROD-EMERGENCY feature gate that emits **exactly one** quick-loot input and then proves an effect via **semantic inventory evidence** (or aborts).

- Mode: `FRBOT_MODE=looting_basic`
- Evidence (preferred): semantic inventory delta (items ↑ or `capacity_used` ↑)
- Fallback (bounded): chat delta only when inventory becomes unreadable AFTER

Recommended runner (REAL + OBS source identity):

```powershell
./scripts/run_looting_basic_real_obs_source.ps1 \
  -WindowHwnd "0x3094a" \
  -ObsSourceName "Tibia_Fuente" \
  -ConfigPath "./rois_prod_emergency_looting_basic.json" \
  -LootGesture "alt_q" \
  -QuickLootKey "R" \
  -DumpFrames
```

Prerequisites for PASS (REAL certification):

- Place a corpse with guaranteed GOLD under the character (so `gold_after > gold_before` or `cap_used_after > cap_used_before`).
- In prod_emergency, the implementation forces Alt+Q as the certified gesture; ensure Alt+Q triggers Quick Loot in this client configuration.
- Ensure the OBS source includes the binary inventory overlay (0xBEEF evidence); otherwise the run aborts with `inventory_overlay_missing`.

Artifacts:

- `diagnostics/frames_emergency/` (before/after)
- `diagnostics/runtime.log` (JSONL)

More details: `docs/looting_basic_prod_emergency.md`

REAL execution prerequisites (not vendored in this repo):

- `pip install mss pynput`

REAL execution also requires an ROI config file:

- Generate a starter config:
  - `poetry run python scripts/generate_rois.py --out diagnostics/rois.json --layout default --monitor 1`
- Point runtime at it:
  - PowerShell: `$env:FRBOT_CONFIG_PATH = "diagnostics/rois.json"`
  - CMD: `set FRBOT_CONFIG_PATH=diagnostics\\rois.json`

Calibration checklist: `docs/ROI_CALIBRATION.md`

## PROD-FULL (Windows-only)

`prod_full` is a stricter production profile intended to be **REAL-certifiable** via `tools/audit_prod_full.py` with **evidence artifacts as the authority**.

Dedicated auditor for the FULL pipeline: `tools/audit_prod_full.py`.

Certification pipeline (fail-fast):

- `targeting_full` → `healing_full` → `combat_full` → `cavebot_full` → `looting_full` → `deposit_full` → `trade_full`

One-command launcher (REAL + OBS source identity):

```powershell
./scripts/run_prod_full_real_obs_source.ps1 \
  -WindowHwnd "0x3094a" \
  -ObsSourceName "Tibia_Fuente" \
  -ConfigPath "./config/rois_prod_full.json" \
  -DumpFrames
```

Evidence output:

- `diagnostics/frames_full/evidence_<timestamp>/` (PPM BEFORE/AFTER pairs + `evidence_manifest.json` + per-gate `*_last_result.json`)
- `diagnostics/evidence_<timestamp>.*.out` (precheck / run / audit transcripts)

### Release (prod_full)

Official single-command release gate (prints exactly one line: `RELEASE_GO` or `RELEASE_NO_GO:<reason>`):

```powershell
./tools/run_release_prod_full.ps1 -ObsSource "Tibia_Fuente" -WindowTitle "Tibia - Onniwabanshu"
```

Notes:

- The release runner enables best-effort window focusing by default (`FRBOT_TRY_FOCUS=1`) to support unattended runs. If you want to disable any focus-stealing attempts, set `FRBOT_TRY_FOCUS=0`.
- Runtime-only tuning currently applied by the release orchestrator is documented in `docs/prod_full_runtime_tuning_runbook.md`.

### Core REAL gate (single command)

For a fast REAL projector-backed core certification gate (targeting/healing/cavebot basic), use:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_real_core_tests.ps1 `
  -ObsSource "Tibia_Fuente" `
  -WindowTitle "Tibia - Onniwabanshu" `
  -ConfigPath ".\rois_projector_1920x1009.json" `
  -MaxTicks 5 `
  -GraceSeconds 3 `
  -StrictSafe `
  -DumpFrames
```

Expected terminal tail on pass:

- `RunnerExit: 0`
- `FINAL DECISION: OPERATIONAL_REAL`

Artifacts are written to:

- `diagnostics/frames_real/<timestamp>/`

The wrapper exits with code `0` only when `FINAL DECISION: OPERATIONAL_REAL`.

What it does (stops on first failure):

- `poetry run pytest -q`
- `poetry run python tools/audit_repo_status.py`
- `poetry run python main.py`
- `poetry run python tools/audit_prod_full.py`

Outputs:

- Frames dir: `diagnostics/frames_full/<timestamp>/`
- Release zip: `diagnostics/releases/<timestamp>.zip`

The zip contains:

- `status_repo.json`, `window_diagnostics.json`
- `runtime.log`, `fatal.log`
- `*_last_result.json`
- `*.ppm`

ROI config requirements (REAL, `FRBOT_PROFILE=prod_full`):

- Required base: `minimap`, `battle_list`, `target_frame`, `hp_mp`
- Allowlisted extras used by the pipeline: `inventory_text`, `depot_container`, `trade_inventory`, `trade_npc`, `trade_action`
- Combat evidence requires at least one of: `combat_feedback` or `target_hp_bar`

### Hunger Guard (auto-eat)

Standalone mode to detect hunger state from a ROI and press an eat hotkey repeatedly with cooldown:

```powershell
poetry run python hunger_entrypoint.py
```

Main env vars:

- `FRBOT_HUNGER_ROI` (default: `hunger_status`)
- `FRBOT_HUNGER_RGB` (default: `255,170,0`)
- `FRBOT_HUNGER_RGB_TOL` (default: `28`)
- `FRBOT_HUNGER_MATCH_RATIO_MIN` (default: `0.08`)
- `FRBOT_EAT_KEY` (default: `F9`)
- `FRBOT_EAT_INTERVAL_MS` (default: `1200`)
- `FRBOT_HUNGER_MAX_TICKS` (default: `1200`)

Example:

```powershell
$env:FRBOT_HUNGER_BACKEND='real'
$env:FRBOT_CONFIG_PATH='config/rois_prod_full.json'
$env:FRBOT_HUNGER_ROI='hunger_status'
$env:FRBOT_EAT_KEY='F9'
poetry run python hunger_entrypoint.py
```

### Real calibration (Tibia 15.x) — strict foreground-safe

`tools/calibrate_all_real.py` enforces a strict security invariant:

- The Tibia window HWND must be the *foreground* window at verification time.

This is correct and non-negotiable. A common operational issue is that launching from an interactive terminal/IDE makes the terminal foreground, which triggers a hard stop.

Recommended launcher (does not create a visible console window):

```powershell
./tools/run_calibration_hidden.ps1
```

Deterministic HWND mode (skips title search):

- Set `FRBOT_WINDOW_HWND` to a decimal or hex value (e.g. `0x000E1234`).

Required inputs:

- `FRBOT_REAL_FRAMES_DIR` (absolute path)
- `FRBOT_CONFIG_PATH` (absolute path)
- `FRBOT_WINDOW_HWND` or `FRBOT_WINDOW_TITLE`

Bot config (waypoints):

- `FRBOT_BOT_CONFIG_PATH` points to a legacy `Waypoints/file.json`-style config containing waypoints.
- Cavebot supports world-coordinate waypoints when minimap localization is configured.

World-coordinate cavebot/localization (TibiaMaps):

- `FRBOT_TIBIA_MAP_DATA_DIR`: directory for `tibiamaps/tibia-map-data` (must include `bounds.json` + `floor-XX-map.png` / `floor-XX-path.png`).
- `FRBOT_CAVEBOT_WAYPOINT_SPACE=world`: interpret route waypoints as absolute world coordinates (`world_x/world_y/world_z`).
- `FRBOT_CAVEBOT_LOCALIZE_MIN_SCORE`: runtime confidence threshold for minimap→world localization in cavebot runner.
- `FRBOT_ROUTE_LOCALIZE_MIN_SCORE`: recorder/UI threshold used by the `World lock` indicator and world waypoint annotation.

Desktop UI notes:

- Settings now includes `World lock min score`, which writes `FRBOT_ROUTE_LOCALIZE_MIN_SCORE` and is used in `.env` import/export.
- In the Route Recorder panel, `World lock: ON/OFF` reflects whether the current localization score passes the threshold.

### Smoke test

Run: `./smoke.ps1`

Contract:

- `FRBOT_MODE=real` (with profile guardrails) must abort and produce `diagnostics/fatal.log`.
- `FRBOT_MODE=mock` must exit cleanly with code `0`.

## What is deliberately NOT implemented

- Real screen capture (DXGI/dxcam) adapters
- Real GUI automation inputs (pyautogui / focus-dependent clicking)
- OCR/OBS/virtual display dependencies

Rationale: these are non-deterministic and not verifiable by default; the runtime refuses to start without verified adapters.
