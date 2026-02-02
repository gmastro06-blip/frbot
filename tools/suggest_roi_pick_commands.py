from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from diagnostics import evidence_inventory


_CRITICAL_ROIS: tuple[str, ...] = (
    "minimap",
    "battle_list",
    "inventory_text",
    "hp_bar",
    "mp_bar",
    "trade_npc",
    "trade_inventory",
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_rois(config_path: Path) -> dict[str, Any]:
    data = _read_json(config_path)
    rois = data.get("rois") if isinstance(data, dict) else None
    if not isinstance(rois, dict):
        raise SystemExit("config_invalid_schema")
    return rois


def _find_sample_image(frames_dir: Path, gate: str) -> Path | None:
    # Prefer *_before.ppm.
    items = sorted(frames_dir.glob(f"{gate}_*_before.ppm"))
    if items:
        return items[0]
    items = sorted(frames_dir.glob(f"{gate}_*.ppm"))
    return items[0] if items else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Suggest commands to pick missing ROIs from real frames")
    ap.add_argument("--frames-dir", required=True, help="Directory containing flat .ppm frames (e.g. diagnostics/frames_15y)")
    ap.add_argument("--config", required=True, help="ROI config JSON (e.g. rois_15y.json)")
    ap.add_argument(
        "--shell",
        choices=("powershell", "cmd"),
        default="powershell",
        help="Command style to output",
    )
    args = ap.parse_args(argv)

    frames_dir = Path(str(args.frames_dir)).resolve()
    config_path = Path(str(args.config)).resolve()

    if not frames_dir.exists():
        raise SystemExit(f"frames_dir_missing:{frames_dir}")
    if not config_path.exists():
        raise SystemExit(f"config_missing:{config_path}")

    rois_node = _load_rois(config_path)

    # Missing per gate.
    missing_by_gate: dict[str, list[str]] = {}
    for gate in getattr(evidence_inventory, "_GATES", ()):  # noqa: SLF001
        required = evidence_inventory._roi_names_for_gate(str(gate))  # noqa: SLF001
        miss = [r for r in required if r not in rois_node]
        if miss:
            missing_by_gate[str(gate)] = miss

    missing_critical = [r for r in _CRITICAL_ROIS if r not in rois_node]

    # Print a simple plan: one pick command per missing ROI.
    lines: list[str] = []
    lines.append(f"# frames_dir={frames_dir}")
    lines.append(f"# config={config_path}")

    if not missing_by_gate and not missing_critical:
        lines.append("# No missing ROIs. ROI READY")
        sys.stdout.write("\n".join(lines) + "\n")
        return 0

    if missing_critical:
        lines.append("# Missing semantic-critical ROIs:")
        lines.append("#   " + ", ".join(missing_critical))

    for gate, miss in sorted(missing_by_gate.items()):
        sample = _find_sample_image(frames_dir, gate)
        if sample is None:
            lines.append(f"# WARNING: no sample frame for gate={gate} in {frames_dir}")
            continue
        for roi_name in miss:
            if args.shell == "powershell":
                lines.append(
                    f"powershell -NoProfile -ExecutionPolicy Bypass -File tools\\run_roi_pick.ps1 -Image \"{sample}\" -Name \"{roi_name}\" -ConfigPath \"{config_path}\""
                )
            else:
                lines.append(
                    f"powershell -NoProfile -ExecutionPolicy Bypass -File tools\\run_roi_pick.ps1 -Image \"{sample}\" -Name \"{roi_name}\" -ConfigPath \"{config_path}\""
                )

    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
