# OPERATIONAL GAPS (Facts Only)

This document lists what is missing or unproven for “operational in real mode”. It is intentionally not a roadmap.

## Environment and dependency gaps

- Real mode depends on OS/hardware capabilities (screen capture + input injection). CI cannot validate these.
- `mss` and `pynput` availability is environment-specific; installation alone does not prove permissions or functionality.

## Configuration / calibration gaps

- Real mode requires an ROI config (`FRBOT_CONFIG_PATH`) that defines screen regions used for evidence verification.
- There is no automated calibration; ROI coordinates must be correct for the user’s screen and target application.

## Bot config gaps

- Waypoint-driven cavebot requires a waypoint source (e.g. `FRBOT_BOT_CONFIG_PATH=file.json`).
- There is no validated, game-agnostic bot config schema; only the legacy `file.json` loader is supported.

## Verification gaps

- **Round-trip verification is heuristic**:
  - Preflight verifies `press_noop()` causes a full-frame digest change.
  - Runtime verifies each emitted cavebot move using objective evidence: minimap digest delta (and/or position delta when available).
  - Any digest delta is still not a guarantee the intended window received input (could be unrelated animations).

## Position / navigation gaps

- Cavebot waypoint navigation requires objective position evidence.
  - Mock mode provides position evidence via minimap pixel encoding (deterministic test harness).
  - Real mode does not implement coordinate extraction (OCR / memory reading are intentionally not included), so cavebot progress is limited to “minimap changed” evidence.

## Target selection and window focus gaps

- No robust “target window binding” is proven.
  - There is no guaranteed evidence that capture corresponds to the same window receiving input.
  - Without OS-specific APIs, focus/foreground window correctness is unverified.

## Observability gaps

- Logs are file-based and persistent, but there is no standard schema versioning policy.
- There is no metric export (Prometheus, etc.); only local log inspection.

## Safety and control gaps

- There is no “kill switch” hardware/software guarantee beyond process termination.
- There is no proven rate limiting or guardrails around input emission beyond the minimal adapter contract.

## Input capability gaps

- Real input is keyboard-only; mouse interactions are not implemented.

## CI coverage gaps

- CI can only prove:
  - `FRBOT_MODE=mock` runs and produces runtime evidence.
  - `FRBOT_MODE=real` aborts with persistent fatal evidence (expected on CI).
- CI cannot prove:
  - Real capture recency, monitor correctness, or input permissions.
  - Any actual interaction with a real application.
