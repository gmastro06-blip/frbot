#!/usr/bin/env python3
"""Summarize latency CSV from benchmark_cavebot_latency.py and recommend defaults.

Usage:
    python tools/latency_summary.py latency_sample.csv

Outputs a short report and prints recommended env vars.
"""
from __future__ import annotations

import csv
import math
import sys
from collections import defaultdict
from statistics import mean, median


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return xs[int(k)]
    d0 = xs[int(f)] * (c - k)
    d1 = xs[int(c)] * (k - f)
    return d0 + d1


def load_csv(path: str) -> dict[str, list[float]]:
    out = defaultdict(list)
    with open(path, newline='', encoding='utf-8') as f:
        r = csv.reader(f)
        hdr = next(r, None)
        for row in r:
            if len(row) < 3:
                continue
            stage = row[1]
            try:
                val = float(row[2])
            except Exception:
                continue
            out[stage].append(val)
    return out


def recommend(vals: dict[str, list[float]], base_tick_hz: float = 20.0) -> dict[str, object]:
    cap_input_hz = 120
    cap_vision_hz = 40

    capture_ms = mean(vals.get('capture', []) or [0.0])
    detection_ms = mean(vals.get('detection', []) or [0.0])
    input_ms = mean(vals.get('input', []) or [0.0])

    full_tick_ms = capture_ms + detection_ms + 2.0  # small margin
    if full_tick_ms <= 0.0:
        vision_hz = int(base_tick_hz)
    else:
        vision_hz = int(max(1, math.floor((1000.0 / full_tick_ms) * 0.8)))
    vision_hz = min(vision_hz, cap_vision_hz)

    input_tick_ms = max(input_ms, 0.2) + 0.5
    input_hz = int(max(1, math.floor((1000.0 / input_tick_ms) * 0.8)))
    input_hz = min(input_hz, cap_input_hz)

    # Simple heuristic for reacquire_every
    if detection_ms > 50.0:
        reacq = 8
    elif detection_ms > 20.0:
        reacq = 6
    else:
        reacq = 4

    return {
        'capture_ms': capture_ms,
        'detection_ms': detection_ms,
        'input_ms': input_ms,
        'vision_hz': vision_hz,
        'input_hz': input_hz,
        'reacquire_every': reacq,
    }


def print_report(vals: dict[str, list[float]], rec: dict[str, object]) -> None:
    print('Latency summary:')
    for stage in ('capture', 'detection', 'input'):
        xs = vals.get(stage, [])
        if not xs:
            print(f'  {stage}: no data')
            continue
        print(f'  {stage}: n={len(xs)} mean={mean(xs):.3f} ms p50={percentile(xs,50):.3f} ms p95={percentile(xs,95):.3f} ms')

    print('\nRecommended defaults:')
    print(f"  FRBOT_VISION_HZ={rec['vision_hz']}  # capture+detect mean ~{rec['capture_ms']+rec['detection_ms']:.2f} ms")
    print(f"  FRBOT_INPUT_HZ={rec['input_hz']}  # input mean ~{rec['input_ms']:.3f} ms")
    print(f"  FRBOT_CAVEBOT_MARKER_REACQUIRE_EVERY={rec['reacquire_every']}  # detection mean ~{rec['detection_ms']:.2f} ms")


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print('Usage: latency_summary.py <latency.csv>')
        return 2
    path = argv[0]
    vals = load_csv(path)
    rec = recommend(vals)
    print_report(vals, rec)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
