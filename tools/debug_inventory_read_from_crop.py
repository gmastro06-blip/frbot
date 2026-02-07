from __future__ import annotations

import sys
from pathlib import Path


def _ensure_repo_root_on_syspath() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


_ensure_repo_root_on_syspath()

from contracts.capture import Frame
from contracts.evidence import Roi
from diagnostics.ppm import read_ppm
from runtime.inventory_semantics import read_inventory, read_inventory_pair


def _frame_from_ppm(path: Path) -> Frame:
    img = read_ppm(path)
    return Frame(width=int(img.width), height=int(img.height), monotonic_ts_ns=0, digest_hex="", rgb=bytes(img.rgb))


def main() -> int:
    before_crop = Path(
        "diagnostics/roi_crops/looting_basic_20260205-191923_loot_unverified_action_before__inventory_text.ppm"
    )
    after_crop = Path(
        "diagnostics/roi_crops/looting_basic_20260205-191923_loot_unverified_action_after__inventory_text.ppm"
    )
    if not before_crop.exists() or not after_crop.exists():
        raise SystemExit("missing crop(s)")

    b = _frame_from_ppm(before_crop)
    a = _frame_from_ppm(after_crop)

    roi = Roi(name='inventory_text', x=0, y=0, width=int(b.width), height=int(b.height))

    inv_b = read_inventory(b, roi)
    inv_a = read_inventory(a, roi)
    pair = read_inventory_pair(b, a, roi)

    print("before", before_crop)
    print("after ", after_crop)
    print("read_inventory(before)", inv_b)
    print("read_inventory(after) ", inv_a)
    print("read_inventory_pair   ", pair)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
