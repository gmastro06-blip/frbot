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

```bash
set FRBOT_MODE=mock
poetry run python main.py
```

Mock verification toggles (to force abort paths):

- `FRBOT_MOCK_CAPTURE_OK=0` → abort: capture not verified
- `FRBOT_MOCK_INPUT_OK=0` → abort: input not verified

Logs:

- diagnostics/runtime.log
- diagnostics/fatal.log

Real mode prerequisites (not vendored in this repo):

- `pip install mss pynput`

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
