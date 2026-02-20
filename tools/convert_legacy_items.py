#!/usr/bin/env python3
"""Convert legacy waypoint 'items' lists into runtime `waypoints` arrays.

Usage: run without args to process a hardcoded list (thais_walk.json, thais_circle.json),
or pass file paths on the command line.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def find_items(obj: Any):
    """Recursively find the first 'items' list in a nested dict structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "items" and isinstance(v, list):
                return v
            res = find_items(v)
            if res is not None:
                return res
    elif isinstance(obj, list):
        for el in obj:
            res = find_items(el)
            if res is not None:
                return res
    return None


def item_to_wp(it: dict[str, Any]) -> dict[str, Any]:
    coord = it.get("coordinate") or it.get("coords") or []
    x = coord[0] if len(coord) > 0 else 0
    y = coord[1] if len(coord) > 1 else 0
    z = coord[2] if len(coord) > 2 else 0
    wp: dict[str, Any] = {
        "type": it.get("type", "walk"),
        "x": int(x),
        "y": int(y),
        "z": int(z),
        "enabled": it.get("enabled", True),
        "options": it.get("options", {}),
    }
    if "label" in it and it.get("label"):
        wp.setdefault("options", {}).setdefault("label", it["label"])
    return wp


def convert_file(path: Path) -> bool:
    text = path.read_text()
    obj = json.loads(text)

    items = find_items(obj)
    if not items:
        print(f"no items found in {path}")
        return False

    wps = []
    for it in items:
        if isinstance(it, dict):
            wps.append(item_to_wp(it))

    out = {}
    # preserve existing metadata if present
    if isinstance(obj, dict) and obj.get("metadata"):
        out["metadata"] = obj["metadata"]
    else:
        out["metadata"] = {"schema_version": 1, "recorded_at": datetime.utcnow().isoformat()}

    out["name"] = path.stem
    out["run_to_target"] = False
    out["waypoints"] = wps

    out_path = path.with_suffix(path.suffix + ".fixed.json")
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"wrote fixed: {out_path}")
    return True


def main(argv: list[str]) -> None:
    if len(argv) > 1:
        targets = [Path(p) for p in argv[1:]]
    else:
        targets = [
            ROOT / "Waypoints" / "thais_walk.json",
            ROOT / "Waypoints" / "thais_circle.json",
        ]

    for t in targets:
        if not t.exists():
            print(f"missing: {t}")
            continue
        convert_file(t)


if __name__ == "__main__":
    main(sys.argv)
