# PROD_FULL promotion — PRECHECK (inventory)

Goal: promote `FRBOT_MODE=prod_full` to a FULL, evidence-or-abort, Windows-only operational profile with strict capture+input invariants and a dedicated auditor.

This document is a **snapshot of what already exists** in the repo vs. what is missing for the new spec.

## YA_EXISTE

### Gate runners / semantics (evidence-or-abort)

- Targeting gate (feature/"only" mode):
  - Entry: `run_targeting_only()` in `targeting_entrypoint.py`
  - Preflight: `runtime/targeting_preflight.py`
  - Runner: `runtime/targeting_runner.py` (`execute_intent()` records BEFORE/AFTER and validates semantic evidence)
  - Input authority: `binding.assert_bound()` is enforced before inputs (and also per tick)

- Healing gate (feature/"only" mode):
  - Entry: `run_healing_only()` in `healing_entrypoint.py`
  - Preflight: `runtime/healing_preflight.py`
  - Runner: `runtime/healing_runner.py` (`execute_heal_intent()` evidence-or-abort; asserts bound before input)

- Combat gate (feature/"only" mode):
  - Entry: `run_combat_only()` in `combat_entrypoint.py`
  - Preflight: `runtime/combat_preflight.py` (requires locked target + cooldown + HP/MP readability)
  - Runner: `runtime/combat_runner.py` (exactly one input per intent; asserts bound before input)

- Cavebot gate (feature/"only" mode):
  - Entry: `run_cavebot_only()` in `cavebot_entrypoint.py`
  - Preflight: `runtime/cavebot_preflight.py` (minimap ROI + marker detectable + explicit waypoints)
  - Runner: `runtime/cavebot_runner.py` (strong binding before capture/input; evidence-or-abort with trace)

- Existing FULL production gates already implemented:
  - `looting_full_entrypoint.py`
  - `deposit_full_entrypoint.py`
  - `trade_full_entrypoint.py`

- New FULL gate entrypoints (evidence-or-abort + per-gate last_result):
  - `targeting_full_entrypoint.py`
  - `healing_full_entrypoint.py`
  - `combat_full_entrypoint.py`
  - `cavebot_full_entrypoint.py`

### Capture + input plumbing

- Capture source abstraction already exists:
  - `runtime/capture_source.py` supports `FRBOT_CAPTURE_SOURCE=obs_source` and resolves OBS projector/input HWND.
  - Targeting preflight supports `ObsSourceRealCapture` when `capture_source()=="obs_source"`.

- Input is HWND-bound (Win32):
  - `Win32HwndKeyboard` is used widely in preflights.
  - `resolve_input_hwnd()` exists and is used for REAL input authority.

### ROI loading + schema enforcement (prod profiles)

- Runtime ROI config supports canonical-ish schema:
  - `runtime/config_loader.py` loads JSON and supports a root `{"frame": {"width":..,"height":..}, "rois": {...}}` shape.

- Prod ROI allowlist enforcement exists:
  - `runtime/roi_contract.py::validate_prod_emergency_real_rois_in_bounds()` applies to both `FRBOT_PROFILE=prod_emergency` and `FRBOT_PROFILE=prod_full`.
  - It enforces required base ROIs (minimap/battle_list/target_frame/hp_mp) and rejects unknown extras; prod_full allows a broader explicit superset.

### Existing auditor + evidence inventory (current prod_full pipeline)

- `tools/audit_all.py` (authority) + tests.
- `diagnostics/evidence_inventory.py` already enforces prod_full “operational REAL” requirements for the **current reduced pipeline**.
- `scripts/run_prod_full_real_obs_source.ps1` exists as a one-command runner and currently runs:
  - `combat_basic → looting_full → deposit_full → trade_full` then `tools/audit_all.py`.

- Dedicated FULL auditor exists:
  - `tools/audit_prod_full.py` with exact final line `FINAL DECISION: OPERATIONAL_REAL|NOT_OPERATIONAL_REAL`

- Blocking policy test exists:
  - `tests/unit/test_no_time_sleep_runtime.py` (no `time.sleep()` under `runtime/**`)

## FALTA (para el nuevo spec FULL)

### 1) Gate naming / FULL gates

DONE:
- FULL gates implemented: `targeting_full`, `healing_full`, `combat_full`, `cavebot_full`.
- Existing FULL gates still present: `looting_full`, `deposit_full`, `trade_full`.

### 2) Router: `FRBOT_MODE=prod_full` must run FULL pipeline

DONE:
- `FRBOT_MODE=prod_full` runs FULL pipeline fail-fast:
  `targeting_full → healing_full → combat_full → cavebot_full → looting_full → deposit_full → trade_full`

### 3) Dedicated auditor for prod_full

DONE:
- `tools/audit_prod_full.py` exists and enforces gate-by-gate artifacts.

### 4) Canonical ROI config path/file

DONE:
- Canonical file exists at `config/rois_prod_full.json`.
- Launcher prefers `config\rois_prod_full.json` and keeps a legacy fallback.

### 5) Capture invariant for prod_full: OBS source identity only

DONE:
- PROD REAL startup guards now reject non-`obs_source` capture for prod profiles.

### 6) Evidence artifacts per FULL gate

DONE:
- `targeting_full/healing_full/combat_full/cavebot_full` emit per-gate `*_last_result.json` and BEFORE/AFTER PPMs.

### 7) Tests / CI constraints

PARTIAL:
- Blocking test added for: no `time.sleep` in `runtime/**`.
- Windows CI wiring for FULL prod_full audit is still a separate pipeline configuration task (not implemented here).

## RIESGO / NOTAS

- Enforcing `FRBOT_CAPTURE_SOURCE=obs_source` strictly for `FRBOT_PROFILE=prod_full` may break existing local workflows that used `mss`/OBS projector capture; this is intended by the new spec but is a behavioral change.
- Making `config/rois_prod_full.json` the canonical default will require updating scripts and docs (and possibly keeping backward compatibility for `./rois_prod_full.json` to avoid sharp edges).
- Tightening required ROIs for FULL gates can increase preflight failures until ROI calibration tooling is used (tools already exist: `tools/calibrate_obs_projector_rois.py`, `tools/convert_obs_projector_rois_to_runtime.py`).
- Focus/binding flakiness: the code already asserts binding before inputs; the remaining risk is “foreground waiting without stealing focus” behavior in runners vs. strictness in prod_full.

## Definition of Done (Release gate)

Release is considered “DONE” when the single command below can be executed on Windows with **no human intervention**:

```powershell
./tools/run_release_prod_full.ps1 -ObsSource "Tibia_Fuente" -WindowTitle "Tibia - Onniwabanshu"
```

Contract:

- Output: exactly one line: `RELEASE_GO` or `RELEASE_NO_GO:<reason>`
- Exit codes:
  - `0` = GO (audit says `FINAL DECISION: OPERATIONAL_REAL`)
  - `1` = NO-GO (pipeline/audit ran but is NOT operational)
  - `2` = NOT READY / infrastructure / missing env/config/window selector
- Foreground/focus: the release orchestrator enables best-effort focus attempts by default (`FRBOT_TRY_FOCUS=1`) to support unattended runs. Opt out with `FRBOT_TRY_FOCUS=0`.
- Steps (fail-fast): `pytest -q` → `tools/audit_repo_status.py` → `main.py` → `tools/audit_prod_full.py` → zip
- Mandatory artifacts (zipped at `diagnostics/releases/<timestamp>.zip`):
  - `diagnostics/status_repo.json`, `diagnostics/window_diagnostics.json`
  - `diagnostics/runtime.log` (JSONL), `diagnostics/fatal.log` (JSON, when present)
  - `diagnostics/frames_full/<timestamp>/*_last_result.json` + referenced `*.ppm`
