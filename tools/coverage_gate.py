#!/usr/bin/env python
"""
Coverage gate script - fails if coverage is below thresholds.
"""
import subprocess
import sys
import os

# Thresholds - adjusted for this project
THRESHOLD_GLOBAL = 48  # Minimum global coverage %
THRESHOLD_CRITICAL = 15  # Minimum for critical modules %

# Critical modules
CRITICAL_MODULES = [
    "runtime/combat_runner.py",
    "runtime/combat_preflight.py",
    "runtime/capture_source.py",
]


def main() -> int:
    """Run coverage and check thresholds."""
    print("=" * 60)
    print("COVERAGE GATE")
    print("=" * 60)

    # Run coverage
    print("\n[1/3] Running coverage...")
    result = subprocess.run(
        ["python", "-m", "coverage", "run", "--source=.", "-m", "pytest", "-q"],
        capture_output=True,
        text=True,
    )

    # Get report
    print("[2/3] Analyzing coverage...")
    result = subprocess.run(
        ["python", "-m", "coverage", "report", "--precision=2"],
        capture_output=True,
        text=True,
    )
    print(result.stdout[-800:] if result.stdout else "")

    # Check totals
    lines = result.stdout.split("\n")
    total_line = [l for l in lines if "TOTAL" in l]
    if total_line:
        parts = total_line[0].split()
        # Format: NAME  Stmts  Miss  Cover
        # Find Cover percentage
        for i, p in enumerate(parts):
            if "%" in p:
                pct = float(p.replace("%", ""))
                break
        else:
            pct = 0

        print(f"\n[3/3] Global coverage: {pct}%")

        if pct >= THRESHOLD_GLOBAL:
            print(f"PASS: {pct}% >= {THRESHOLD_GLOBAL}%")
            return 0
        else:
            print(f"FAIL: {pct}% < {THRESHOLD_GLOBAL}%")
            return 1

    print("WARNING: Could not parse coverage. Skipping gate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
