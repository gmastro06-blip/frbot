# BASELINE (STABLE)

**Internal tag (documentation only):**

BASELINE_STABLE = True

This repository is frozen as a stable baseline.

## What is covered (guaranteed by tests)

The following feature-gates are implemented and verified via deterministic tests:

- Targeting
- Healing
- Combat
- Cavebot (minimap + marker tracking)
- Looting (premium + free)
- Deposit

Cross-cutting invariants (contract-level):

- Preflight runs before runtime execution.
- `diagnostics/runtime.log` is created **only** after preflight succeeds.
- `diagnostics/fatal.log` is always written on abort/crash.
- Evidence-or-abort discipline:
  - 1 intent → 1 input → 1 AFTER capture → semantic evidence validation, or abort.
- MockWorld is deterministic and covered by tests.

## What must NOT be changed (baseline freeze)

Do not modify these areas in-place when adding new features:

- Contracts and invariants: `contracts/*`
- Core engine semantics: `core/engine.py`, `contracts/engine.py`
- Runtime config/state definitions: `contracts/runtime.py`
- Preflight modules and logging invariants
- Existing gate loops (tick loops, attempt guardrails, evidence checks)
- Existing abort reasons (strings are part of the contract)

If something “could be better”, ignore it in baseline work. This baseline is intentionally boring.

## How this repo may be extended (only via new gates)

All new features must be introduced as a **new independent gate**, with:

- A new `<gate>_entrypoint.py`
- A new `runtime/<gate>_preflight.py`
- A new `runtime/<gate>_runner.py`
- Deterministic MockWorld flags/state required for that gate
- Blocking pytest coverage for success + abort taxonomy + no-spam limits

Rules:

- Do not modify existing gate loops.
- Do not reuse evidence extracted for one gate as “success” evidence for another gate.
- Do not relax preflight/logging invariants.

## CI baseline

CI is intentionally minimal and must remain deterministic:

- `python -m pytest -q`
- `./smoke.ps1`

No new jobs, no parallelization, no new linters.
