#!/usr/bin/env python3
"""Simple benchmark to measure capture, detection and input latencies.

Usage:
    python tools/benchmark_cavebot_latency.py --iters 100 --out latency.csv

The script runs preflight to obtain adapters, then measures durations for:
 - capture.grab()
 - select_player_marker (detection)
 - input.press_noop()

Results are appended to a CSV with columns: ts_iso,stage,duration_ms
"""
from __future__ import annotations

import argparse
import csv
import datetime
import sys
from typing import Optional

from pathlib import Path

# Ensure repo root is on sys.path when running this script directly.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.cavebot_preflight import run as cavebot_preflight_run
from runtime.cavebot_semantics import select_player_marker
from runtime.env_bootstrap import load_repo_env
from contracts.runtime import RuntimeContext, RuntimeStatus, RuntimeTelemetry, RuntimeState
from cavebot_entrypoint import _load_cavebot_config_from_env

load_repo_env()


def now_iso() -> str:
    return datetime.datetime.now().isoformat()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--iters", type=int, default=200)
    p.add_argument("--out", type=str, default="latency.csv")
    args = p.parse_args(argv)

    # Build a minimal RuntimeContext (preflight expects populated fields).
    cfg = _load_cavebot_config_from_env()
    ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())
    try:
        capture, input_, binding = cavebot_preflight_run(ctx)
    except Exception:
        # Fall back to deterministic mock adapters when preflight fails (e.g., missing ROIs).
        from adapters.capture.mock import MockCapture
        from adapters.input.mock import MockInput
        from adapters.window.mock import MockWindowBinding

        capture = MockCapture(True)
        input_ = MockInput(True)
        binding = MockWindowBinding()

    rows: list[tuple[str, str, float]] = []

    for i in range(int(args.iters)):
        ts = now_iso()
        # capture
        t0 = __import__("time").perf_counter()
        frame = capture.grab()
        t1 = __import__("time").perf_counter()
        rows.append((ts, "capture", (t1 - t0) * 1000.0))

        # detection (use select_player_marker)
        t0 = __import__("time").perf_counter()
        try:
            _ = select_player_marker(frame, marker_rgb=(255, 0, 255), tol=30, min_pixels=1, max_pixels=0)
        except Exception:
            pass
        t1 = __import__("time").perf_counter()
        rows.append((ts, "detection", (t1 - t0) * 1000.0))

        # input noop
        t0 = __import__("time").perf_counter()
        try:
            input_.press_noop()
        except Exception:
            pass
        t1 = __import__("time").perf_counter()
        rows.append((ts, "input", (t1 - t0) * 1000.0))

    # Write CSV
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts_iso", "stage", "duration_ms"])
        for r in rows:
            w.writerow([r[0], r[1], f"{r[2]:.6f}"])

    print(f"Wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())