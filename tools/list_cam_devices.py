from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Allow running as a script without installing the package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Probe camera/capture devices by index (OpenCV)")
    ap.add_argument("--max-index", type=int, default=10, help="Max device index to probe")
    ap.add_argument("--backend", default="dshow", choices=["any", "dshow", "msmf"], help="OpenCV backend")
    args = ap.parse_args(argv)

    try:
        import cv2  # type: ignore
    except Exception as exc:
        print(json.dumps({"ok": False, "reason": "missing_dependency", "detail": str(exc)}, ensure_ascii=False))
        return 2

    api = cv2.CAP_ANY
    if args.backend == "dshow":
        api = cv2.CAP_DSHOW
    elif args.backend == "msmf":
        api = cv2.CAP_MSMF

    results = []
    for idx in range(0, max(0, int(args.max_index)) + 1):
        cap = cv2.VideoCapture(int(idx), int(api))
        opened = bool(cap is not None and cap.isOpened())
        item: dict[str, object] = {"index": int(idx), "opened": opened}
        if opened:
            # Try a couple reads.
            ok = False
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            for _ in range(3):
                rok, frame = cap.read()
                if rok and frame is not None:
                    ok = True
                    h2, w2 = frame.shape[:2]
                    w = int(w2) or w
                    h = int(h2) or h
                    break
                time.sleep(0.05)
            item.update({"read_ok": bool(ok), "size": [int(w), int(h)]})
        try:
            cap.release()
        except Exception:
            pass
        results.append(item)

    print(json.dumps({"ok": True, "backend": str(args.backend), "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
