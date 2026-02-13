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
from runtime import inventory_semantics as inv


def _frame(path: Path) -> Frame:
    img = read_ppm(path)
    return Frame(width=int(img.width), height=int(img.height), monotonic_ts_ns=0, digest_hex="", rgb=bytes(img.rgb))


def main() -> int:
    before_crop = Path(
        "diagnostics/roi_crops/looting_basic_20260205-192538_loot_unverified_action_before__inventory_text.ppm"
    )
    after_crop = Path(
        "diagnostics/roi_crops/looting_basic_20260205-192538_loot_unverified_action_after__inventory_text.ppm"
    )
    if not before_crop.exists() or not after_crop.exists():
        raise SystemExit("missing crop(s) for 192538 run")

    b = _frame(before_crop)
    a = _frame(after_crop)
    roi = Roi(name="inventory_text", x=0, y=0, width=int(b.width), height=int(b.height))

    templates = inv._load_digit_templates(Path(inv._visual_templates_path()))
    if not templates:
        print("no templates loaded")
        return 2

    h = int(roi.height)
    mid = h // 2

    def cap_remaining(fr: Frame) -> int | None:
        v = inv._read_line_number_from_roi(fr, roi, y0=mid, y1=h, templates=templates)
        if v is None:
            v = inv._read_line_number_from_roi(fr, roi, y0=0, y1=h, templates=templates)
        return None if v is None else int(v)

    cap_b = cap_remaining(b)
    cap_a = cap_remaining(a)

    print("before_crop", before_crop)
    print("after_crop ", after_crop)
    print("cap_remaining_before", cap_b)
    print("cap_remaining_after ", cap_a)
    if cap_b is not None and cap_a is not None:
        print("delta(after-before)", int(cap_a - cap_b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
