#!/usr/bin/env python3
"""Conservative auto-fixer for Waypoints JSON files.

Rules:
- If a file has a top-level `waypoints` list, clamp any negative x/y/z to 0.
- If `waypoints` is missing or not a list, skip (don't invent data).
- Ensure `metadata` exists and `schema_version` is set to 1 when missing.
- Write fixes to `<original>.fixed.json` to avoid overwriting originals.

Produces `diagnostics/waypoints_autofix.json` summarizing changes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DIAG = ROOT / "diagnostics" / "waypoints_validation.json"
OUT = ROOT / "diagnostics" / "waypoints_autofix.json"


def clamp_waypoint(wp: dict[str, Any]) -> bool:
    changed = False
    for k in ("x", "y", "z"):
        if k in wp and isinstance(wp[k], (int, float)):
            if wp[k] < 0:
                wp[k] = 0
                changed = True
    return changed


def main() -> None:
    if not DIAG.exists():
        print(f"Diagnostics not found: {DIAG}")
        raise SystemExit(1)

    data = json.loads(DIAG.read_text())
    results: dict[str, Any] = {"summary": {"total": 0, "fixed": 0, "skipped": 0}, "files": {}}

    for entry in data.get("files", []):
        results["summary"]["total"] += 1
        raw = entry.get("file") or entry.get("path") or entry.get("relpath") or ""
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        try:
            rel = path.relative_to(ROOT)
        except Exception:
            rel = path
        file_rec: dict[str, Any] = {"path": str(rel), "fixed": False, "reason": None}

        if not path.exists():
            file_rec["reason"] = "missing"
            results["summary"]["skipped"] += 1
            results["files"][str(rel)] = file_rec
            continue

        try:
            obj = json.loads(path.read_text())
        except Exception as e:
            file_rec["reason"] = f"parse_error: {e}"
            results["summary"]["skipped"] += 1
            results["files"][str(rel)] = file_rec
            continue

        is_top_list = isinstance(obj, list)
        if is_top_list:
            wps = obj
        else:
            wps = obj.get("waypoints")

        if not isinstance(wps, list):
            file_rec["reason"] = "no_waypoints_list"
            results["summary"]["skipped"] += 1
            results["files"][str(rel)] = file_rec
            continue

        changed_any = False
        for wp in wps:
            if isinstance(wp, dict):
                if clamp_waypoint(wp):
                    changed_any = True

        if not is_top_list:
            if not obj.get("metadata"):
                obj.setdefault("metadata", {})
                obj["metadata"]["schema_version"] = 1
                changed_any = True
            else:
                if "schema_version" not in obj["metadata"]:
                    obj["metadata"]["schema_version"] = 1
                    changed_any = True

        if changed_any:
            out_path = path.with_suffix(path.suffix + ".fixed.json")
            # preserve list-or-dict output form
            out_obj = obj if not is_top_list else wps
            out_path.write_text(json.dumps(out_obj, indent=2, ensure_ascii=False))
            file_rec["fixed"] = True
            file_rec["out"] = str(out_path.relative_to(ROOT))
            results["summary"]["fixed"] += 1
        else:
            file_rec["reason"] = "no_changes_needed"
            results["summary"]["skipped"] += 1

        results["files"][str(rel)] = file_rec

    OUT.write_text(json.dumps(results, indent=2))
    print(f"Wrote autofix summary to: {OUT}")


if __name__ == "__main__":
    main()
