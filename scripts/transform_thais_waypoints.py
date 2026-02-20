from __future__ import annotations

import json
import logging
from pathlib import Path
from statistics import mean
from typing import Any

import sys
from pathlib import Path as _P

# Ensure repository root is on sys.path when running as a script
repo_root = str(_P(__file__).resolve().parents[1])
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from runtime.tibia_map_index import TibiaMapIndex

LOG = logging.getLogger(__name__)


def load_waypoints(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_waypoints(data: dict[str, Any], path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def fetch_markers() -> list[dict[str, Any]]:
    import urllib.request

    url = "https://raw.githubusercontent.com/tibiamaps/tibia-map-data/main/data/markers.json"
    with urllib.request.urlopen(url) as r:
        return json.load(r)


def find_thais_markers(markers: list[dict]) -> list[dict]:
    out = []
    for m in markers:
        # inspect common text fields
        for f in ("description", "label", "title", "name", "id"):
            v = m.get(f)
            if isinstance(v, str) and "thais" in v.lower():
                out.append(m)
                break
    return out


def main() -> None:
    src = Path("Waypoints/Thais_circulo.json")
    dst = Path("Waypoints/Thais_circulo_world.json")
    data = load_waypoints(src)
    waypoints = data.get("waypoints", [])
    locals_xy = [(int(w.get("x", 0)), int(w.get("y", 0))) for w in waypoints if (int(w.get("x", 0)) != 0 or int(w.get("y", 0)) != 0)]
    if not locals_xy:
        # fallback: include zeros
        locals_xy = [(int(w.get("x", 0)), int(w.get("y", 0))) for w in waypoints]

    mean_x = int(round(mean([p[0] for p in locals_xy])))
    mean_y = int(round(mean([p[1] for p in locals_xy])))

    print(f"Local centroid: ({mean_x},{mean_y}) based on {len(locals_xy)} points")

    markers = fetch_markers()
    print(f"markers JSON type: {type(markers)}")
    if isinstance(markers, dict):
        print("marker dict keys sample:", list(markers.keys())[:20])
    thais = find_thais_markers(markers)
    if not thais:
        print("No Thais markers found in tibia-map-data/markers.json")
        print("Dumping first 50 marker entries (name/title/label fields) for inspection:")
        for i, m in enumerate(markers[:50]):
            # try several common fields
            info = m.get("name") or m.get("title") or m.get("label") or m.get("id")
            print(i, info)
        print("Sample marker objects (first 5):")
        for m in markers[:5]:
            print(repr(m))
        return

    index = TibiaMapIndex()


    candidates = []
    # evaluate candidates: align marker to centroid and compute metrics
    for m in thais:
        mx = int(m.get("x", 0))
        my = int(m.get("y", 0))
        mz = int(m.get("z", 0) or 0)
        anchor_x = mx - mean_x
        anchor_y = my - mean_y

        matched = 0
        total = 0
        dists = []
        for w in waypoints:
            lx = int(w.get("x", 0))
            ly = int(w.get("y", 0))
            wz = int(w.get("z", data.get("metadata", {}).get("recorder", {}).get("default_z", 0)))
            wx = anchor_x + lx
            wy = anchor_y + ly
            total += 1
            try:
                walk = index.is_walkable(wx, wy, wz)
                if walk:
                    matched += 1
                sx, sy, sz = index.snap_tile(wx, wy, wz)
                # euclidean distance in tiles
                dist = ((sx - wx) ** 2 + (sy - wy) ** 2) ** 0.5
                dists.append(dist)
            except Exception:
                dists.append(float('nan'))

        mean_dist = float('nan') if not dists else sum(d for d in dists if not (d != d)) / len(dists)
        score = matched
        candidates.append({
            'marker': m,
            'anchor': (anchor_x, anchor_y, mz),
            'matched': matched,
            'total': total,
            'matched_ratio': matched / max(1, total),
            'mean_snap_distance': mean_dist,
        })

    # sort by matched_ratio, then by mean_snap_distance (lower better)
    candidates.sort(key=lambda c: (-c['matched_ratio'], c['mean_snap_distance']))
    best = candidates[0] if candidates else None
    if not best:
        print("No suitable anchor candidates")
        return
    print("Top candidates:")
    for i, c in enumerate(candidates[:5]):
        name = c['marker'].get('name') or c['marker'].get('description') or None
        print(i + 1, name, 'anchor=', c['anchor'], 'matched=', c['matched'], 'ratio=', round(c['matched_ratio'], 3), 'mean_dist=', round(c['mean_snap_distance'] or 0, 3))
    anchor_x, anchor_y, anchor_z = best['anchor']
    marker = best['marker']
    print(f"Selected anchor based on marker '{marker.get('name')}' -> anchor ({anchor_x},{anchor_y})")

    # Transform waypoints and also produce debug details
    new_wp = []
    debug_items = []
    for idx, w in enumerate(waypoints):
        lx = int(w.get("x", 0))
        ly = int(w.get("y", 0))
        lz = int(w.get("z", data.get("metadata", {}).get("recorder", {}).get("default_z", 0)))
        wx = anchor_x + lx
        wy = anchor_y + ly
        wz = lz  # keep original local z unless you want to map to marker.z
        # attempt snapping to nearest walkable
        snapped = index.snap_tile(wx, wy, wz)
        w2 = dict(w)
        w2["x"] = int(snapped[0])
        w2["y"] = int(snapped[1])
        w2["z"] = int(snapped[2])
        # annotate
        w2.setdefault("options", {})
        w2["options"]["snapped_from_local"] = {"local_x": lx, "local_y": ly, "anchor_marker": marker.get("name")}
        new_wp.append(w2)

        dist = ((snapped[0] - wx) ** 2 + (snapped[1] - wy) ** 2) ** 0.5
        debug_items.append({
            'index': idx,
            'local': {'x': lx, 'y': ly, 'z': lz},
            'target_world': {'x': wx, 'y': wy, 'z': wz},
            'snapped': {'x': int(snapped[0]), 'y': int(snapped[1]), 'z': int(snapped[2])},
            'snap_distance': float(dist),
        })

    data_out = dict(data)
    data_out["waypoints"] = new_wp
    data_out.setdefault("metadata", {})
    data_out["metadata"]["map_transform"] = {
        "anchor_marker": marker.get("name"),
        "anchor_world": {"x": anchor_x, "y": anchor_y, "z": anchor_z},
        "local_centroid": {"x": mean_x, "y": mean_y},
        "matched_score": best['matched'],
    }

    # write main transformed file
    save_waypoints(data_out, dst)
    # write debug JSON
    dbg_path = dst.with_name(dst.stem + "_debug.json")
    dbg = {
        'anchor_candidate': {'marker': marker, 'anchor': {'x': anchor_x, 'y': anchor_y, 'z': anchor_z}},
        'candidates_top': candidates[:8],
        'items': debug_items,
    }
    save_waypoints(dbg, dbg_path)
    print(f"Wrote transformed waypoints to {dst} and debug to {dbg_path}")


if __name__ == "__main__":
    main()
