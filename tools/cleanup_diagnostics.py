"""Safe cleanup for generated diagnostics and transient artifacts.

Deletes only known generated locations under the repo root. Safe to run.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path.cwd()
TARGETS = [
    ROOT / "diagnostics",
    ROOT / "out",
    ROOT / "latency_long.csv",
    ROOT / "latency_sample.csv",
    ROOT / "runtime_ui.log",
]


def safe_remove(path: Path) -> None:
    if not path.exists():
        return
    if path.is_file():
        try:
            path.unlink()
            print(f"removed file: {path}")
        except Exception as exc:
            print(f"failed to remove file {path}: {exc}")
        return
    # Directory - only remove if it looks like generated (contains 'waypoints' or jsonl/json files or ppm)
    try:
        # conservative check: directory contains at least one .json, .jsonl, .ppm or startswith 'waypoints'
        candidates = list(path.rglob("*.json")) + list(path.rglob("*.jsonl")) + list(path.rglob("*.ppm"))
        if not candidates and not any(p.name.startswith("waypoints_") for p in path.iterdir() if p.is_dir()):
            print(f"skipping directory (no generated artifacts found): {path}")
            return
    except Exception:
        pass
    try:
        shutil.rmtree(path)
        print(f"removed directory: {path}")
    except Exception as exc:
        print(f"failed to remove directory {path}: {exc}")


def main() -> None:
    for t in TARGETS:
        safe_remove(t)


if __name__ == "__main__":
    main()
