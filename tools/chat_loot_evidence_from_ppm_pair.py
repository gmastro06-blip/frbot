from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _ensure_repo_root_on_syspath() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return repo_root


_ensure_repo_root_on_syspath()

from diagnostics.ppm import read_ppm  # noqa: E402
from runtime.chat_loot_semantics import _crop_box, _dhash64, _find_changed_bands  # noqa: E402


def _load_rois(config_path: Path) -> dict:
    data = json.loads(config_path.read_text(encoding='utf-8'))
    rois = data.get('rois') or {}
    return dict(rois)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--before', required=True)
    ap.add_argument('--after', required=True)
    ap.add_argument('--config', required=True)
    ap.add_argument('--roi', default='chat_loot_area')
    ap.add_argument('--out-patterns', default='')
    ap.add_argument('--tolerance', type=int, default=6)
    ap.add_argument('--prefix-w', type=int, default=220)
    args = ap.parse_args()

    before = read_ppm(Path(args.before))
    after = read_ppm(Path(args.after))
    if before.width != after.width or before.height != after.height:
        raise SystemExit('before/after dimensions differ')

    rois = _load_rois(Path(args.config))
    roi = rois.get(str(args.roi))
    if not roi:
        raise SystemExit(f'roi not found: {args.roi}')

    x = int(roi['x'])
    y = int(roi['y'])
    w = int(roi['width'])
    h = int(roi['height'])

    # Crop ROI.
    before_crop = _crop_box(before.rgb, w=before.width, h=before.height, x0=x, y0=y, x1=x + w - 1, y1=y + h - 1)
    after_crop = _crop_box(after.rgb, w=after.width, h=after.height, x0=x, y0=y, x1=x + w - 1, y1=y + h - 1)

    boxes = _find_changed_bands(before_crop, after_crop, w=w, h=h)

    prefix_w = max(40, min(int(args.prefix_w), int(w)))

    candidates = []
    hashes = []
    for (x0, y0, x1, y1) in boxes:
        x1p = min(int(x1), int(x0) + int(prefix_w) - 1)
        patch_after = _crop_box(after_crop, w=w, h=h, x0=x0, y0=y0, x1=x1p, y1=y1)
        bw = int(x1p - x0 + 1)
        bh = int(y1 - y0 + 1)
        ha = _dhash64(patch_after, w=bw, h=bh)
        candidates.append({'box': [x0, y0, x1, y1], 'box_prefix': [x0, y0, x1p, y1], 'dhash_after': hex(ha)})
        hashes.append(ha)

    payload = {
        'roi': str(args.roi),
        'prefix_w': int(prefix_w),
        'tolerance': int(args.tolerance),
        'candidates': candidates,
        'count': int(len(candidates)),
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.out_patterns:
        out = {
            'tolerance': int(args.tolerance),
            'dhashes': [{'name': 'chat_loot_candidate', 'hex': hex(int(h))} for h in hashes],
        }
        Path(args.out_patterns).write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'Wrote patterns: {args.out_patterns}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
