from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is first on sys.path (avoid collisions with installed modules).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from diagnostics.ppm import read_ppm


def ppm_has_beef(ppm_path: Path) -> bool:
    img = read_ppm(ppm_path)
    rgb = img.rgb
    # Scan only pixel starts (RGB triplets). Magic is little-endian 0xBEEF => bytes EF BE.
    for i in range(0, len(rgb) - 6, 3):
        if rgb[i] == 0xEF and rgb[i + 1] == 0xBE:
            return True
    return False


def main() -> int:
    roots = [Path("diagnostics/frames_emergency"), Path("diagnostics/frames"), Path("diagnostics/roi_crops")]
    ppm_paths: list[Path] = []
    for r in roots:
        if r.exists():
            ppm_paths.extend(sorted(r.rglob("*.ppm")))

    print({"ppm_files": len(ppm_paths)})

    hits: list[str] = []
    for idx, p in enumerate(ppm_paths, start=1):
        try:
            if ppm_has_beef(p):
                hits.append(p.as_posix())
                print({"hit": p.as_posix()})
                if len(hits) >= 20:
                    break
        except Exception:
            continue
        if idx % 1000 == 0:
            print({"scanned": idx})

    print({"total_hits": len(hits)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
