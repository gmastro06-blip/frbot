# OPERATIONAL GAPS (Facts Only)

This document lists what is missing or unproven for “operational in real mode”. It is intentionally not a roadmap.

## Environment and dependency gaps

- Real mode depends on OS/hardware capabilities (screen capture + input injection). CI cannot validate these.
- `mss` and `pynput` availability is environment-specific; installation alone does not prove permissions or functionality.

## Verification gaps

- **Round-trip verification is heuristic**: it assumes that a no-op keypress (or similar) should cause a frame digest change.
  - On many systems it may not change the pixels at all (no visible focus indicator, no UI reaction), causing false aborts.
  - Conversely, a digest change does not prove the *intended* window received input (could be unrelated animation).

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

## CI coverage gaps

- CI can only prove:
  - `FRBOT_MODE=mock` runs and produces runtime evidence.
  - `FRBOT_MODE=real` aborts with persistent fatal evidence (expected on CI).
- CI cannot prove:
  - Real capture recency, monitor correctness, or input permissions.
  - Any actual interaction with a real application.
