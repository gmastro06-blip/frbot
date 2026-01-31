from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class ScreenSize:
    width: int
    height: int


def _clamp_int(v: int, *, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


def _roi(
    *,
    name: str,
    x: int,
    y: int,
    width: int,
    height: int,
    screen: ScreenSize,
) -> Dict[str, int]:
    # Keep within bounds and non-empty.
    x = _clamp_int(x, lo=0, hi=max(0, screen.width - 1))
    y = _clamp_int(y, lo=0, hi=max(0, screen.height - 1))
    width = max(1, min(width, screen.width - x if screen.width > x else 1))
    height = max(1, min(height, screen.height - y if screen.height > y else 1))
    return {"x": x, "y": y, "width": width, "height": height}


def _get_screen_size_from_mss(monitor_index: int) -> Tuple[Optional[ScreenSize], Optional[str]]:
    try:
        import mss  # type: ignore
    except Exception as exc:
        return None, f"mss not available ({type(exc).__name__}: {exc})"

    try:
        with mss.mss() as sct:
            monitors = getattr(sct, "monitors", None)
            if not monitors or monitor_index >= len(monitors):
                return None, f"monitor_index {monitor_index} not available (monitors={len(monitors) if monitors else 0})"

            mon = monitors[monitor_index]
            w = int(mon.get("width", 0) or 0)
            h = int(mon.get("height", 0) or 0)
            if w <= 0 or h <= 0:
                return None, "invalid monitor size from mss"
            return ScreenSize(width=w, height=h), None
    except Exception as exc:
        return None, f"failed to query mss monitors ({type(exc).__name__}: {exc})"


def _generate_layout_default(screen: ScreenSize) -> Dict[str, Dict[str, int]]:
    """A conservative starter layout.

    This is NOT game-specific and will not be correct for most setups.
    It is only meant to unblock the first real-mode run and allow you to
    iteratively calibrate ROIs.
    """

    w, h = screen.width, screen.height

    # Sizes are intentionally small-ish, so calibration is easier.
    bar_w = max(20, int(w * 0.18))
    bar_h = max(4, int(h * 0.012))

    minimap_size = max(64, int(min(w, h) * 0.18))

    inv_w = max(100, int(w * 0.20))
    inv_h = max(100, int(h * 0.28))

    rois: Dict[str, Dict[str, int]] = {}

    rois["hp_bar"] = _roi(
        name="hp_bar",
        x=int(w * 0.03),
        y=int(h * 0.03),
        width=bar_w,
        height=bar_h,
        screen=screen,
    )
    rois["mana_bar"] = _roi(
        name="mana_bar",
        x=int(w * 0.03),
        y=int(h * 0.03) + bar_h + max(2, int(h * 0.006)),
        width=bar_w,
        height=bar_h,
        screen=screen,
    )

    rois["target_indicator"] = _roi(
        name="target_indicator",
        x=int(w * 0.45),
        y=int(h * 0.03),
        width=max(20, int(w * 0.08)),
        height=max(20, int(h * 0.06)),
        screen=screen,
    )

    rois["loot_indicator"] = _roi(
        name="loot_indicator",
        x=int(w * 0.45),
        y=int(h * 0.10),
        width=max(20, int(w * 0.08)),
        height=max(20, int(h * 0.06)),
        screen=screen,
    )

    rois["minimap"] = _roi(
        name="minimap",
        x=max(0, w - minimap_size - int(w * 0.02)),
        y=int(h * 0.02),
        width=minimap_size,
        height=minimap_size,
        screen=screen,
    )

    rois["inventory"] = _roi(
        name="inventory",
        x=max(0, w - inv_w - int(w * 0.02)),
        y=max(0, h - inv_h - int(h * 0.02)),
        width=inv_w,
        height=inv_h,
        screen=screen,
    )

    # Trade/depot are often near right/bottom UI; these are placeholders.
    rois["trade"] = _roi(
        name="trade",
        x=max(0, w - inv_w - int(w * 0.02)),
        y=max(0, int(h * 0.40)),
        width=inv_w,
        height=max(60, int(h * 0.18)),
        screen=screen,
    )

    rois["depot"] = _roi(
        name="depot",
        x=max(0, w - inv_w - int(w * 0.02)),
        y=max(0, int(h * 0.60)),
        width=inv_w,
        height=max(40, int(h * 0.10)),
        screen=screen,
    )

    return rois


def _generate_layout_fullframe(screen: ScreenSize) -> Dict[str, Dict[str, int]]:
    roi = {"x": 0, "y": 0, "width": screen.width, "height": screen.height}
    return {
        "hp_bar": dict(roi),
        "mana_bar": dict(roi),
        "target_indicator": dict(roi),
        "loot_indicator": dict(roi),
        "minimap": dict(roi),
        "inventory": dict(roi),
        "trade": dict(roi),
        "depot": dict(roi),
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Generate a starter FRBOT ROI config JSON.")
    p.add_argument("--out", default="diagnostics/rois.json", help="Output path for ROI JSON")
    p.add_argument(
        "--layout",
        choices=["default", "fullframe"],
        default="default",
        help="ROI layout preset. 'default' is a rough starter; 'fullframe' is a blunt fallback.",
    )
    p.add_argument(
        "--monitor",
        type=int,
        default=1,
        help="mss monitor index to use (1 is usually the primary monitor).",
    )
    p.add_argument(
        "--window-title",
        default="",
        help="Optional: size ROIs to the client area of the first window whose title contains this substring.",
    )
    p.add_argument("--screen-width", type=int, default=0, help="Override screen width")
    p.add_argument("--screen-height", type=int, default=0, help="Override screen height")

    args = p.parse_args(argv)

    screen: ScreenSize
    mss_note: Optional[str]

    if args.window_title:
        try:
            from adapters.window.win32 import Win32WindowBinding

            binding = Win32WindowBinding(title_substring=str(args.window_title))
            vr = binding.verify()
            if vr.ok:
                snap = binding.snapshot()
                screen = ScreenSize(width=int(snap.rect.width), height=int(snap.rect.height))
                mss_note = f"Sized to window client rect via title substring: {args.window_title!r}"
            else:
                screen = ScreenSize(width=1920, height=1080)
                mss_note = f"window binding failed for {args.window_title!r}; used fallback screen size 1920x1080"
        except Exception as exc:
            screen = ScreenSize(width=1920, height=1080)
            mss_note = f"window-title sizing failed ({type(exc).__name__}: {exc}); used fallback screen size 1920x1080"

    elif args.screen_width > 0 and args.screen_height > 0:
        screen = ScreenSize(width=int(args.screen_width), height=int(args.screen_height))
        mss_note = None
    else:
        screen_opt, mss_note = _get_screen_size_from_mss(int(args.monitor))
        if screen_opt is None:
            # Fall back to something reasonable, but be explicit.
            screen = ScreenSize(width=1920, height=1080)
        else:
            screen = screen_opt

    if args.layout == "default":
        rois = _generate_layout_default(screen)
    else:
        rois = _generate_layout_fullframe(screen)

    payload: Dict[str, Any] = {
        "rois": rois,
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "screen": {"width": screen.width, "height": screen.height},
            "layout": args.layout,
            "monitor": int(args.monitor),
            "note": (
                mss_note
                or "This is a starter config. You MUST calibrate ROI coordinates to match your application/window."
            ),
        },
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote ROI config to: {out_path}")

    if mss_note:
        print(f"Note: {mss_note}")
        print("Used fallback screen size 1920x1080.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
