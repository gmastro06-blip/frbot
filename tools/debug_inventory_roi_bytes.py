from __future__ import annotations

import json
import sys
from pathlib import Path

def _ensure_repo_root_on_syspath() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


_ensure_repo_root_on_syspath()

from diagnostics.ppm import read_ppm


def main() -> int:
    ppm_path = Path("diagnostics/frames/looting_basic_20260205-191923_loot_unverified_action_before.ppm")
    cfg = json.loads(Path("rois_prod_emergency_looting_basic.json").read_text("utf-8"))
    roi = cfg["rois"]["inventory_text"]

    img = read_ppm(ppm_path)
    w = int(img.width)
    row_stride = w * 3
    start = int(roi["y"]) * row_stride + int(roi["x"]) * 3
    blob = img.rgb[start : start + 12]

    print("ppm", str(ppm_path))
    print("roi", roi)
    print("first12", list(blob))
    magic = int.from_bytes(blob[0:2], "little", signed=False)
    print("magic", hex(magic))
    print("u16[1]=gold", int.from_bytes(blob[2:4], "little"), "u16[2]=cap_used", int.from_bytes(blob[4:6], "little"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
