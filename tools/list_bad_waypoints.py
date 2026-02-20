#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAG = ROOT / "diagnostics" / "waypoints_validation.json"

def main():
    data = json.loads(DIAG.read_text())
    bad = []
    for entry in data.get("files", []):
        if not entry.get("valid", True):
            bad.append({"file": entry.get("file"), "errors": entry.get("errors")})
    for b in bad:
        print(b["file"])
        for e in b["errors"]:
            print("  -", e)
    print(f"\nTotal bad files: {len(bad)}")

if __name__ == '__main__':
    main()
