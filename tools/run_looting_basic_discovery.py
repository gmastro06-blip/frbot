from __future__ import annotations

from importlib import import_module

try:
    _bootstrap_mod = import_module("tools._bootstrap")
except ModuleNotFoundError:
    _bootstrap_mod = import_module("_bootstrap")

_bootstrap_mod.bootstrap_tool_env(__file__)

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

# Allow running this tool as `python tools/run_looting_basic_discovery.py` without
# needing to set PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from runtime.env import parse_window_hwnd_env
from runtime.looting_basic_preflight import looting_basic_preflight
from runtime.looting_basic_runner import execute_looting_basic_once


def _wait_until_foreground(*, expected_hwnd: int, wait_ms: int) -> bool:
    wait_ms = max(0, int(wait_ms))
    expected_hwnd = int(expected_hwnd)
    if wait_ms <= 0 or expected_hwnd <= 0:
        return True

    try:
        from adapters.windows import win32 as w32

        deadline = time.monotonic() + (wait_ms / 1000.0)
        last_print = 0.0
        while time.monotonic() < deadline:
            fg = int(w32.get_foreground_window() or 0)
            if fg == expected_hwnd:
                return True

            now = time.monotonic()
            if now - last_print >= 1.0:
                last_print = now
                fg_title = ""
                try:
                    fg_title = str(w32.get_window_text(int(fg)) or "") if fg > 0 else ""
                except Exception:
                    fg_title = ""
                exp_title = ""
                try:
                    exp_title = str(w32.get_window_text(int(expected_hwnd)) or "") if expected_hwnd > 0 else ""
                except Exception:
                    exp_title = ""

                print(
                    json.dumps(
                        {
                            "waiting_foreground": True,
                            "expected_hwnd": hex(int(expected_hwnd)) if expected_hwnd > 0 else "0x0",
                            "expected_title": exp_title,
                            "foreground_hwnd": hex(int(fg)) if fg > 0 else "0x0",
                            "foreground_title": fg_title,
                            "remaining_ms": int(max(0.0, (deadline - time.monotonic()) * 1000.0)),
                        },
                        ensure_ascii=False,
                    )
                )
            time.sleep(0.1)
        return False
    except Exception:
        return True


def _ts() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def _default_points(frame_w: int, frame_h: int) -> list[tuple[int, int]]:
    cx = int(frame_w // 2)
    cy = int(frame_h // 2)

    # Small grid around center + a bias slightly below center.
    offsets = [
        (0, 0),
        (-40, 0),
        (40, 0),
        (0, -40),
        (0, 40),
        (-40, -40),
        (40, -40),
        (-40, 40),
        (40, 40),
        (0, 80),
        (-40, 80),
        (40, 80),
    ]

    pts: list[tuple[int, int]] = []
    for dx, dy in offsets:
        x = max(0, min(cx + int(dx), int(frame_w) - 1))
        y = max(0, min(cy + int(dy), int(frame_h) - 1))
        if (x, y) not in pts:
            pts.append((x, y))
    return pts


def _roi_grid_points(*, roi: Any, frame_w: int, frame_h: int, rows: int = 3, cols: int = 3, margin: int = 6) -> list[tuple[int, int]]:
    """Generate a small grid of click points inside a ROI.

    Used to avoid requiring manual coordinates in REAL runs.
    """
    try:
        rx = int(getattr(roi, 'x', 0) or 0)
        ry = int(getattr(roi, 'y', 0) or 0)
        rw = int(getattr(roi, 'width', 0) or 0)
        rh = int(getattr(roi, 'height', 0) or 0)
    except Exception:
        rx, ry, rw, rh = 0, 0, 0, 0

    rows = max(1, int(rows))
    cols = max(1, int(cols))
    margin = max(0, int(margin))

    inner_w = max(1, rw - 2 * margin)
    inner_h = max(1, rh - 2 * margin)
    ox = rx + margin
    oy = ry + margin

    pts: list[tuple[int, int]] = []
    for r in range(rows):
        for c in range(cols):
            # Centers evenly spaced across the ROI interior.
            fx = (c + 0.5) / float(cols)
            fy = (r + 0.5) / float(rows)
            x = ox + int(round(fx * inner_w))
            y = oy + int(round(fy * inner_h))
            x = max(0, min(int(x), int(frame_w) - 1))
            y = max(0, min(int(y), int(frame_h) - 1))
            if (x, y) not in pts:
                pts.append((x, y))

    # Prefer center-ish points first.
    cx = rx + (rw // 2)
    cy = ry + (rh // 2)
    pts.sort(key=lambda p: (abs(p[0] - cx) + abs(p[1] - cy), abs(p[0] - cx), abs(p[1] - cy)))
    return pts


def _tile_lattice_points(
    *,
    center_x: int,
    center_y: int,
    frame_w: int,
    frame_h: int,
    step: int = 32,
    rings: int = 3,
) -> list[tuple[int, int]]:
    """Generate a tile-aligned lattice around a center point.

    In Tibia, adjacent SQMs are commonly ~32px apart on screen in classic mode.
    Using a 32px lattice increases chances of landing on tile centers vs a
    generic ROI grid.
    """
    step = max(1, int(step))
    rings = max(0, int(rings))

    cx = int(center_x)
    cy = int(center_y)

    pts: list[tuple[int, int]] = []
    for ry in range(-rings, rings + 1):
        for rx in range(-rings, rings + 1):
            x = cx + (rx * step)
            y = cy + (ry * step)
            x = max(0, min(int(x), int(frame_w) - 1))
            y = max(0, min(int(y), int(frame_h) - 1))
            pts.append((x, y))

    # Sort by lattice distance (center first).
    pts.sort(key=lambda p: (abs(p[0] - cx) + abs(p[1] - cy), abs(p[0] - cx), abs(p[1] - cy)))

    # De-dup preserving order.
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []
    for p in pts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _parse_points(raw: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for part in (raw or "").split(";"):
        s = part.strip()
        if not s:
            continue
        if "," not in s:
            raise ValueError(f"Invalid point '{s}', expected 'x,y'")
        xs, ys = s.split(",", 1)
        out.append((int(xs.strip()), int(ys.strip())))
    return out


def _parse_point(raw: str) -> tuple[int, int]:
    s = (raw or "").strip()
    if not s or "," not in s:
        raise ValueError("Invalid point, expected 'x,y'")
    xs, ys = s.split(",", 1)
    return int(xs.strip()), int(ys.strip())


def _grid_around(*, x: int, y: int, frame_w: int, frame_h: int, radius: int, step: int) -> list[tuple[int, int]]:
    r = max(0, int(radius))
    s = max(1, int(step))

    pts: list[tuple[int, int]] = []
    for dy in range(-r, r + 1, s):
        for dx in range(-r, r + 1, s):
            if abs(dx) + abs(dy) > r:
                continue
            px = max(0, min(int(x) + int(dx), int(frame_w) - 1))
            py = max(0, min(int(y) + int(dy), int(frame_h) - 1))
            pts.append((px, py))

    # Sort by distance to center (center first).
    pts.sort(key=lambda p: (abs(p[0] - int(x)) + abs(p[1] - int(y)), abs(p[0] - int(x)), abs(p[1] - int(y))))

    # De-dup preserving order.
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []
    for p in pts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _run_once(
    *,
    ctx: RuntimeContext,
    cap: Any,
    inp: Any,
    binding: Any,
    x: int,
    y: int,
    frames_dir: Path,
) -> dict:
    os.environ["FRBOT_LOOTING_BASIC_LOOT_X"] = str(int(x))
    os.environ["FRBOT_LOOTING_BASIC_LOOT_Y"] = str(int(y))
    os.environ["FRBOT_REAL_FRAMES_DIR"] = str(frames_dir)

    started = time.monotonic()
    try:
        out = execute_looting_basic_once(ctx, capture=cap, input_=inp, binding=binding)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": bool(out.ok),
            "error": None,
            "x": int(x),
            "y": int(y),
            "evidence_kind": str(out.evidence_kind),
            "inventory_before": None if out.inventory_before is None else asdict(out.inventory_before),
            "inventory_after": None if out.inventory_after is None else asdict(out.inventory_after),
            "delta": None if out.delta is None else asdict(out.delta),
            "attempts_used": int(getattr(ctx.looting, "attempts_used", 0)),
            "frames_dir": str(frames_dir.as_posix()),
            "elapsed_ms": int(elapsed_ms),
        }
    except PreflightFailed as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        details = getattr(exc, "details", None)
        return {
            "ok": False,
            "error": str(exc),
            "details": details,
            "x": int(x),
            "y": int(y),
            "attempts_used": int(getattr(ctx.looting, "attempts_used", 0)),
            "frames_dir": str(frames_dir.as_posix()),
            "elapsed_ms": int(elapsed_ms),
        }


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover a working looting_basic clickpoint in REAL, then optionally run one certified attempt.")
    ap.add_argument("--certify", action="store_true", help="After discovery success, run exactly one certified attempt at the discovered point.")
    ap.add_argument("--points", default="", help="Semicolon-separated list of points 'x,y;x,y;...' in frame coordinates. If omitted, uses a small default grid.")
    ap.add_argument("--around", default="", help="Single point 'x,y' to generate a discovery grid around (frame coordinates). Ignored if --points is set.")
    ap.add_argument("--radius", type=int, default=96, help="Radius (px) for --around grid (Manhattan distance).")
    ap.add_argument("--step", type=int, default=24, help="Grid step (px) for --around.")
    ap.add_argument("--max-attempts", type=int, default=12, help="Max discovery attempts.")
    ap.add_argument(
        "--wait-foreground-ms",
        type=int,
        default=30000,
        help="Wait up to this many ms for FRBOT_WINDOW_HWND to become the foreground window (does not steal focus). Use 0 to disable.",
    )
    ap.add_argument(
        "--coord-space",
        choices=["frame", "screen"],
        default="frame",
        help=(
            "Coordinate space for click points. 'frame' means capture-frame pixels (recommended for discovery). "
            "'screen' means absolute screen pixels (use only if you measured ClickXY in screen coordinates)."
        ),
    )
    ap.add_argument("--obs-source", default="", help="OBS source name (FRBOT_OBS_SOURCE_NAME). If empty, uses env.")
    ap.add_argument("--config", default="rois_prod_emergency_looting_basic.json", help="ROI config path (FRBOT_CONFIG_PATH).")
    args = ap.parse_args()

    # Ensure evidence dumps are enabled.
    os.environ.setdefault("FRBOT_DUMP_FRAMES", "1")

    # Discovery is intended to find a working clickpoint for click-based gestures
    # (e.g. Shift+RMB). In prod_emergency, looting_basic forces Alt+Q and ignores
    # ClickXY, which makes clickpoint discovery meaningless. So:
    # - Discovery defaults to a non-prod profile.
    # - If --certify is requested, the final certified run switches to prod_emergency.
    os.environ.setdefault("FRBOT_PROFILE", "dev")
    os.environ.setdefault("FRBOT_MODE", "real")
    os.environ.setdefault("FRBOT_CAPTURE_SOURCE", "obs_source")
    if args.obs_source:
        os.environ["FRBOT_OBS_SOURCE_NAME"] = str(args.obs_source)

    # Discovery gesture (click-based).
    os.environ.setdefault("FRBOT_TIBIA_LOOT_GESTURE", "shift_rmb")

    # IMPORTANT: discovery points are typically expressed in capture-frame space.
    # Do not inherit a stale FRBOT_FRAME_COORD_SPACE from the shell.
    os.environ["FRBOT_FRAME_COORD_SPACE"] = str(args.coord_space)

    # In REAL, UI updates may lag; sample AFTER a bit more to avoid false negatives.
    os.environ.setdefault("FRBOT_LOOTING_BASIC_VERIFY_ATTEMPTS", "8")
    os.environ.setdefault("FRBOT_LOOTING_BASIC_VERIFY_DELAY_MS", "250")

    os.environ["FRBOT_CONFIG_PATH"] = str(args.config)

    expected_hwnd = int(parse_window_hwnd_env("FRBOT_WINDOW_HWND"))
    wait_ms = max(0, int(args.wait_foreground_ms))

    # Optionally wait until the target window is foreground.
    _wait_until_foreground(expected_hwnd=expected_hwnd, wait_ms=wait_ms)

    cfg = RuntimeConfig(
        mode="real",
        tick_hz=float(os.environ.get("FRBOT_TICK_HZ", "20.0") or "20.0"),
        config_path=str(os.environ.get("FRBOT_CONFIG_PATH", "") or ""),
        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,
        minimap_roi=str(os.environ.get("FRBOT_MINIMAP_ROI", "minimap") or "minimap"),
        window_hwnd=expected_hwnd,
        window_title_substring=str(os.environ.get("FRBOT_WINDOW_TITLE", "") or ""),
        inventory_text_roi=str(os.environ.get("FRBOT_INVENTORY_TEXT_ROI", "inventory_text") or "inventory_text"),
        quick_loot_key=str(os.environ.get("FRBOT_QUICK_LOOT_KEY", "R") or "R"),
    )
    ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

    cap, inp, binding = looting_basic_preflight(ctx)

    # Determine candidate points.
    fw = int(getattr(ctx, "frame_width", 0) or 0)
    fh = int(getattr(ctx, "frame_height", 0) or 0)
    if fw <= 0 or fh <= 0:
        # Fallback if loader didn't set it.
        f0 = cap.grab()
        fw, fh = int(f0.width), int(f0.height)

    if args.points.strip():
        points = _parse_points(args.points)
    elif args.around.strip():
        ax, ay = _parse_point(args.around)
        points = _grid_around(x=int(ax), y=int(ay), frame_w=int(fw), frame_h=int(fh), radius=int(args.radius), step=int(args.step))
    else:
        # If a loot ROI is configured (area around the player/corpses), prefer
        # clicking within it so operators don't need to provide x,y.
        loot_roi = None
        try:
            loot_roi = ctx.rois.get('loot_corpse')
        except Exception:
            loot_roi = None
        if loot_roi is not None:
            rx = int(getattr(loot_roi, 'x', 0) or 0)
            ry = int(getattr(loot_roi, 'y', 0) or 0)
            rw = int(getattr(loot_roi, 'width', 0) or 0)
            rh = int(getattr(loot_roi, 'height', 0) or 0)
            cx = rx + (rw // 2)
            cy = ry + (rh // 2)

            # Strategy: try a tile-aligned lattice first, then fall back to a
            # dense ROI grid (helps if tile step is not exactly 32px).
            points = []
            points.extend(_tile_lattice_points(center_x=cx, center_y=cy, frame_w=int(fw), frame_h=int(fh), step=32, rings=4))
            points.extend(_roi_grid_points(roi=loot_roi, frame_w=int(fw), frame_h=int(fh), rows=5, cols=5, margin=4))

            # De-dup preserving order.
            seen: set[tuple[int, int]] = set()
            deduped: list[tuple[int, int]] = []
            for p in points:
                if p in seen:
                    continue
                seen.add(p)
                deduped.append(p)
            points = deduped
        else:
            points = _default_points(fw, fh)

    points = points[: max(1, int(args.max_attempts))]

    root = Path("diagnostics") / "frames_emergency" / f"looting_discovery_{_ts()}"
    root.mkdir(parents=True, exist_ok=True)

    manifest_path = root / "discovery_manifest.json"

    def _write_manifest(*, results: list[dict], winner: tuple[int, int] | None) -> None:
        manifest = {
            "ts": _ts(),
            "profile": str(os.environ.get("FRBOT_PROFILE", "")),
            "mode": str(os.environ.get("FRBOT_MODE", "")),
            "capture_source": str(os.environ.get("FRBOT_CAPTURE_SOURCE", "")),
            "obs_source_name": str(os.environ.get("FRBOT_OBS_SOURCE_NAME", "")),
            "config_path": str(os.environ.get("FRBOT_CONFIG_PATH", "")),
            "args": {
                "certify": bool(args.certify),
                "points": str(args.points),
                "around": str(args.around),
                "radius": int(args.radius),
                "step": int(args.step),
                "max_attempts": int(args.max_attempts),
                "wait_foreground_ms": int(args.wait_foreground_ms),
                "coord_space": str(args.coord_space),
            },
            "auto_points": {
                "used": bool((not args.points.strip()) and (not args.around.strip())),
                "strategy": "loot_roi_tile_lattice_then_roi_grid" if (not args.points.strip() and not args.around.strip()) else "manual",
                "tile_step": 32,
                "tile_rings": 4,
                "roi_grid": {"rows": 5, "cols": 5, "margin": 4},
            },
            "winner": None if winner is None else {"x": int(winner[0]), "y": int(winner[1])},
            "results": results,
            "root_frames_dir": str(root.as_posix()),
        }

        tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(manifest_path)

    results: list[dict] = []
    winner: tuple[int, int] | None = None

    # Write an initial manifest so the directory is self-describing even if the
    # process is interrupted early.
    _write_manifest(results=results, winner=winner)

    try:
        for idx, (x, y) in enumerate(points, start=1):
            fg_wait_timed_out = False
            if wait_ms > 0 and not _wait_until_foreground(expected_hwnd=expected_hwnd, wait_ms=wait_ms):
                # Proceed anyway: the input adapter may still be able to
                # foreground the window, and if it can't we'll capture the
                # real error from the runner.
                fg_wait_timed_out = True

            attempt_dir = root / f"discover_{idx:02d}_{int(x)}_{int(y)}"
            res = _run_once(ctx=ctx, cap=cap, inp=inp, binding=binding, x=int(x), y=int(y), frames_dir=attempt_dir)
            if fg_wait_timed_out:
                res["foreground_wait_timed_out"] = True
            results.append(res)
            print(json.dumps({"attempt": idx, **res}, ensure_ascii=False))
            _write_manifest(results=results, winner=winner)
            if bool(res.get("ok")):
                winner = (int(x), int(y))
                _write_manifest(results=results, winner=winner)
                break
    finally:
        _write_manifest(results=results, winner=winner)

    if not args.certify or winner is None:
        return 0

    # Certified run: exactly one input, in its own evidence dir.
    os.environ["FRBOT_PROFILE"] = "prod_emergency"
    # For clarity: prod_emergency forces Alt+Q anyway.
    os.environ["FRBOT_TIBIA_LOOT_GESTURE"] = "alt_q"
    certified_dir = root / f"certified_{int(winner[0])}_{int(winner[1])}"
    cert = _run_once(ctx=ctx, cap=cap, inp=inp, binding=binding, x=int(winner[0]), y=int(winner[1]), frames_dir=certified_dir)
    (root / "certified_result.json").write_text(json.dumps(cert, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"certified": True, **cert}, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
