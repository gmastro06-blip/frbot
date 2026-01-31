# frbot (rewrite)

Minimal, contract-driven runtime.

This repository has been rewritten to enforce:

- No dynamic dict state in runtime.
- No GUI/capture automation as a central dependency.
- Fail-fast startup: abort in <1s if environment/contracts are not satisfied.
- Persistent evidence on failure (diagnostics/fatal.log).

## Repo structure (non-negotiable)

- contracts/  → dataclasses + invariants
- core/       → pure logic (no IO, no GUI, no sleeps)
- adapters/   → untrusted external integration points (capture/input)
- runtime/    → state machine + abort paths
- diagnostics/→ persistent logging

## Running

Default is strict and will abort (no real adapters are provided in this rewrite):

```bash
poetry run python main.py
```

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

Mock cavebot waypoints:

- If `FRBOT_BOT_CONFIG_PATH` is not set, mock mode uses a small deterministic waypoint loop (keeps CI/test runs operational).
- If you want to use the legacy waypoint file in this repo: set `FRBOT_BOT_CONFIG_PATH=file.json`.

Mock verification toggles (to force abort paths):

- `FRBOT_MOCK_CAPTURE_OK=0` → abort: capture not verified
- `FRBOT_MOCK_INPUT_OK=0` → abort: input not verified

Logs:

- diagnostics/runtime.log
- diagnostics/fatal.log

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
- Validate HWND binding requirements in real mode
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
- No cavebot/waypoints

Use cases:

- Validate HP/MP ROIs and consistency
- Validate cooldown observability
- Validate HWND binding + safe input with minimal surface area

Real mode prerequisites (not vendored in this repo):

- `pip install mss pynput`

Real mode also requires an ROI config file:

- Generate a starter config:
	- `poetry run python scripts/generate_rois.py --out diagnostics/rois.json --layout default --monitor 1`
- Point runtime at it:
	- PowerShell: `$env:FRBOT_CONFIG_PATH = "diagnostics/rois.json"`
	- CMD: `set FRBOT_CONFIG_PATH=diagnostics\\rois.json`

Calibration checklist: `docs/ROI_CALIBRATION.md`

Bot config (waypoints):

- `FRBOT_BOT_CONFIG_PATH` points to a legacy `file.json`-style config containing waypoints.
- Cavebot requires objective position evidence; this repo does not implement real-mode position extraction (see `docs/OPERATIONAL_GAPS.md`).

### Smoke test

Run: `./smoke.ps1`

Contract:

- `FRBOT_MODE=real` must abort and produce `diagnostics/fatal.log`.
- `FRBOT_MODE=mock` must exit cleanly with code `0`.

## What is deliberately NOT implemented

- Real screen capture (DXGI/dxcam) adapters
- Real GUI automation inputs (pyautogui / focus-dependent clicking)
- OCR/OBS/virtual display dependencies

Rationale: these are non-deterministic and not verifiable by default; the runtime refuses to start without verified adapters.
