#!/usr/bin/env python3
"""Apply fixed waypoint files produced by tools/auto_fix_waypoints.py

Behavior:
- Reads `diagnostics/waypoints_autofix.json`.
- For each entry where `fixed` is true and `out` exists, copies the fixed file over the original.
- Creates a backup `<original>.bak` if one doesn't already exist.
- Writes a summary to `diagnostics/waypoints_apply_fixed.json`.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUTOFIX = ROOT / "diagnostics" / "waypoints_autofix.json"
OUT = ROOT / "diagnostics" / "waypoints_apply_fixed.json"


def main() -> None:
    if not AUTOFIX.exists():
        print(f"Autofix summary not found: {AUTOFIX}")
        raise SystemExit(1)

    data: dict[str, Any] = json.loads(AUTOFIX.read_text())
    results: dict[str, Any] = {"summary": {"total": 0, "applied": 0, "skipped": 0}, "files": {}}

    for rel, info in data.get("files", {}).items():
        results["summary"]["total"] += 1
        try:
            orig = ROOT / info["path"]
        except Exception:
            orig = ROOT / rel

        file_rec: dict[str, Any] = {"path": str(orig), "applied": False, "reason": None}

        if not info.get("fixed"):
            file_rec["reason"] = "not_marked_fixed"
            results["summary"]["skipped"] += 1
            results["files"][rel] = file_rec
            continue

        out_rel = info.get("out")
        if not out_rel:
            file_rec["reason"] = "missing_out_path"
            results["summary"]["skipped"] += 1
            results["files"][rel] = file_rec
            continue

        fixed_path = ROOT / out_rel
        if not fixed_path.exists():
            file_rec["reason"] = f"fixed_missing: {fixed_path}"
            results["summary"]["skipped"] += 1
            results["files"][rel] = file_rec
            continue

        try:
            # Backup original if not already backed up
            bak = orig.with_name(orig.name + ".bak")
            if orig.exists() and not bak.exists():
                shutil.copy2(orig, bak)

            # Copy fixed file over original
            shutil.copy2(fixed_path, orig)
            file_rec["applied"] = True
            file_rec["out"] = str(fixed_path)
            results["summary"]["applied"] += 1
        except Exception as e:
            file_rec["reason"] = f"copy_failed: {e}"
            results["summary"]["skipped"] += 1

        results["files"][rel] = file_rec

    OUT.write_text(json.dumps(results, indent=2))
    print(f"Applied fixes: {results['summary']['applied']} files; summary: {OUT}")


if __name__ == "__main__":
    main()
