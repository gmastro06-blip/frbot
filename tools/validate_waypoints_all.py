from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path.cwd()))
from runtime.navigator_types import validate_waypoints


ROOT = Path.cwd() / "Waypoints"
OUT = Path.cwd() / "diagnostics" / "waypoints_validation.json"


def find_candidate_lists(data: Any):
    """Yield candidate lists that look like waypoint lists.

    Heuristic: any list whose elements are mappings containing 'x' and 'y' keys.
    """
    if isinstance(data, list):
        if data and all(isinstance(e, dict) for e in data):
            # quick check for x/y keys presence in some elements
            if any(('x' in e or 'y' in e) for e in data):
                yield data
        # also descend into list elements
        for e in data:
            yield from find_candidate_lists(e)
    elif isinstance(data, dict):
        for v in data.values():
            yield from find_candidate_lists(v)


def validate_file(p: Path) -> dict[str, object]:
    rec: dict[str, object] = {"file": str(p), "found": 0, "valid": True, "errors": []}
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:
        rec["valid"] = False
        rec["errors"].append(f"json_load_error: {type(exc).__name__}: {exc}")
        return rec

    seen = 0
    any_bad = False
    for lst in find_candidate_lists(data):
        seen += 1
        errs = validate_waypoints(lst)
        if errs:
            any_bad = True
            rec["errors"].append({"index": seen, "errors": errs[:10]})

    rec["found"] = seen
    if seen == 0:
        rec["valid"] = False
        rec["errors"].append("no_waypoints_found")
    elif any_bad:
        rec["valid"] = False

    return rec


def main() -> int:
    out = {"summary": {}, "files": []}
    if not ROOT.exists():
        print(f"Waypoints root not found: {ROOT}")
        return 2
    files = list(ROOT.rglob("*.json"))
    for p in sorted(files):
        r = validate_file(p)
        out["files"].append(r)

    total = len(out["files"]) if out["files"] else 0
    bad = sum(1 for f in out["files"] if not f.get("valid", False))
    out["summary"] = {"total_files": total, "bad_files": bad}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote: {OUT} -> total={total} bad={bad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
