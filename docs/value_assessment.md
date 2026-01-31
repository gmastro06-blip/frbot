# Value Assessment (Evidence-First)

This project’s runtime contract is: **do not operate unless the capture+input surface is verified**, and produce **persistent evidence** for both aborts and successful runs.

## What is measurably valuable

### 1) Fail-fast preflight + persistent fatal evidence
- **Value**: prevents “silent running” when the environment is not operational.
- **Evidence**:
  - `diagnostics/fatal.log` exists after a failed run.
  - Fatal log contains a clear reason string and a traceback.
- **Why it matters**: in automation, the most dangerous state is “appears running but is ineffective.” This contract forces a binary outcome: verified operation or documented abort.

### 2) Deterministic mock mode
- **Value**: enables repeatable CI and regression tests without external dependencies.
- **Evidence**:
  - `FRBOT_MODE=mock` exits `0`.
  - `diagnostics/runtime.log` includes `tick_count >= 1`.
- **Why it matters**: a decision engine and runner can only be improved safely if there is a stable harness that proves behavior over time.

### 3) Pure decision engine (no IO)
- **Value**: the “what should we do?” logic is testable independently of capture/input.
- **Evidence**:
  - Engine code does not import adapters.
  - Unit tests can validate decisions with synthetic inputs.
- **Why it matters**: IO is nondeterministic; separating it from decisions prevents accidental coupling and makes correctness verifiable.

### 4) Observable round-trip verification (capture ↔ input)
- **Value**: reduces the risk of “input is sent but has no effect” (wrong window focus, blocked input, permissions, etc.).
- **Evidence**:
  - Preflight captures `before` and `after` frames.
  - If the digests do not change, the system aborts with evidence.
- **Why it matters**: verifying only “no exception was thrown” is not verification; observable impact is closer to a real operational check.

### 5) Typed intents + per-intent evidence verification
- **Value**: the engine emits typed intents (cavebot/healing/targeting/looting/deposit/trade), and the runtime verifies each emitted input via a capture recapture.
- **Evidence**:
  - Engine returns `EngineOutput(intents=...)` with at most one intent per tick.
  - Runtime executes at most one intent per tick and verifies `EvidenceExpectation` using objective post-conditions:
    - ROI digest deltas when configured
    - Observation deltas (e.g. HP increases, flags become true/false, position changes)
  - If evidence is not observed, the system aborts with persistent fatal evidence.
- **Why it matters**: this prevents “we pressed a key” from being mistaken as “we achieved an effect.”

## What is NOT claimed as value (yet)

- **Bot effectiveness** (speed, accuracy, profit): not evidenced here.
- **Safety against bans / detection**: not evidenced here.
- **Completeness of gameplay logic**: not evidenced here.
- **Out-of-the-box real-mode operation**: requires ROI calibration/config; not auto-detected.
- **Real-mode cavebot navigation**: waypoint navigation requires objective position extraction; not implemented in real mode.

## Current acceptance criteria (what must remain true)

- Preflight is the gatekeeper: no runtime loop without verification.
- No silent fallback to mock when real fails.
- Abort is explicit, persistent, and diagnosable from files.
- Decision logic remains pure and unit-testable.
