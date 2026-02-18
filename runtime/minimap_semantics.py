from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.capture import Frame
from contracts.runtime import Tile


@dataclass(frozen=True, slots=True)
class MarkerConfig:
    rgb: tuple[int, int, int]
    tol: int
    min_pixels: int
    max_pixels: int
    min_fill_ratio: float
    max_aspect_ratio: float


@dataclass(frozen=True, slots=True)
class MinimapPosition:
    px: float
    py: float


@dataclass(frozen=True, slots=True)
class MarkerDetection:
    pos: MinimapPosition
    pixel_count: int
    bbox_left: int
    bbox_top: int
    bbox_right: int
    bbox_bottom: int
    fill_ratio: float
    aspect_ratio: float


def _parse_rgb_triplet(raw: str, *, default: tuple[int, int, int]) -> tuple[int, int, int]:
    s = (raw or '').strip()
    if not s:
        return default
    parts = [p.strip() for p in s.split(',')]
    if len(parts) != 3:
        return default
    try:
        r, g, b = (int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return default
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    return (r, g, b)


def marker_config_from_env(
    rgb_raw: str,
    tol_raw: str,
    min_pixels_raw: str,
    max_pixels_raw: str,
    min_fill_ratio_raw: str,
    max_aspect_ratio_raw: str,
) -> MarkerConfig:
    rgb = _parse_rgb_triplet(rgb_raw, default=(255, 0, 255))
    try:
        tol = int(tol_raw)
    except Exception:
        tol = 30
    tol = max(0, min(255, tol))

    try:
        min_pixels = int(min_pixels_raw)
    except Exception:
        min_pixels = 5
    min_pixels = max(1, min(10_000, min_pixels))

    try:
        max_pixels = int(max_pixels_raw)
    except Exception:
        max_pixels = 0
    max_pixels = max(0, min(1_000_000, max_pixels))

    try:
        min_fill_ratio = float(min_fill_ratio_raw)
    except Exception:
        min_fill_ratio = 0.15
    min_fill_ratio = max(0.0, min(1.0, float(min_fill_ratio)))

    try:
        max_aspect_ratio = float(max_aspect_ratio_raw)
    except Exception:
        max_aspect_ratio = 4.0
    max_aspect_ratio = max(1.0, min(50.0, float(max_aspect_ratio)))

    return MarkerConfig(
        rgb=rgb,
        tol=tol,
        min_pixels=min_pixels,
        max_pixels=max_pixels,
        min_fill_ratio=min_fill_ratio,
        max_aspect_ratio=max_aspect_ratio,
    )


def detect_player_marker(frame: Frame, cfg: MarkerConfig) -> Optional[MarkerDetection]:
    if not frame.minimap_detected:
        return None
    w = int(frame.minimap_width)
    h = int(frame.minimap_height)
    if w <= 0 or h <= 0:
        return None
    rgb = frame.minimap_rgb
    if not rgb:
        return None
    expected_len = w * h * 3
    if len(rgb) < expected_len:
        return None

    tr, tg, tb = cfg.rgb
    tol = int(cfg.tol)

    # Connected-component clustering (4-neighborhood). Take the largest cluster.
    visited = bytearray(w * h)
    best_count = 0
    best_sum_x = 0
    best_sum_y = 0
    best_min_x = 0
    best_max_x = 0
    best_min_y = 0
    best_max_y = 0

    def matches(pix: int) -> bool:
        i = pix * 3
        r = rgb[i]
        g = rgb[i + 1]
        b = rgb[i + 2]
        return abs(int(r) - tr) <= tol and abs(int(g) - tg) <= tol and abs(int(b) - tb) <= tol

    for start in range(w * h):
        if visited[start]:
            continue
        visited[start] = 1
        if not matches(start):
            continue

        stack = [start]
        count = 0
        sum_x = 0
        sum_y = 0
        min_x = w
        min_y = h
        max_x = -1
        max_y = -1

        while stack:
            pix = stack.pop()
            x = pix % w
            y = pix // w
            count += 1
            sum_x += x
            sum_y += y
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y

            # 4-neighbors
            if x > 0:
                n = pix - 1
                if not visited[n] and matches(n):
                    visited[n] = 1
                    stack.append(n)
            if x + 1 < w:
                n = pix + 1
                if not visited[n] and matches(n):
                    visited[n] = 1
                    stack.append(n)
            if y > 0:
                n = pix - w
                if not visited[n] and matches(n):
                    visited[n] = 1
                    stack.append(n)
            if y + 1 < h:
                n = pix + w
                if not visited[n] and matches(n):
                    visited[n] = 1
                    stack.append(n)

        if count > best_count:
            best_count = count
            best_sum_x = sum_x
            best_sum_y = sum_y
            best_min_x = min_x
            best_min_y = min_y
            best_max_x = max_x
            best_max_y = max_y

    if best_count < int(cfg.min_pixels):
        return None

    if int(cfg.max_pixels) > 0 and best_count > int(cfg.max_pixels):
        return None

    bbox_w = (best_max_x - best_min_x + 1) if best_max_x >= best_min_x else 0
    bbox_h = (best_max_y - best_min_y + 1) if best_max_y >= best_min_y else 0
    if bbox_w <= 0 or bbox_h <= 0:
        return None

    fill = float(best_count) / float(bbox_w * bbox_h)
    if fill < float(cfg.min_fill_ratio):
        return None

    aspect = float(max(bbox_w, bbox_h)) / float(min(bbox_w, bbox_h))
    if aspect > float(cfg.max_aspect_ratio):
        return None

    pos = MinimapPosition(px=float(best_sum_x) / float(best_count), py=float(best_sum_y) / float(best_count))
    return MarkerDetection(
        pos=pos,
        pixel_count=int(best_count),
        bbox_left=int(best_min_x),
        bbox_top=int(best_min_y),
        bbox_right=int(best_max_x),
        bbox_bottom=int(best_max_y),
        fill_ratio=float(fill),
        aspect_ratio=float(aspect),
    )


@dataclass
class SemanticTracker:
    pixels_per_tile: float
    z: int = 7

    _origin: Optional[MinimapPosition] = None

    def observe_tile(self, pos: MinimapPosition) -> Tile:
        if self._origin is None:
            self._origin = pos
        origin = self._origin
        assert origin is not None

        ppt = float(self.pixels_per_tile) if self.pixels_per_tile else 1.0
        if ppt <= 0:
            ppt = 1.0

        dx_tiles = int(round((pos.px - origin.px) / ppt))
        dy_tiles = int(round((pos.py - origin.py) / ppt))
        return Tile(x=dx_tiles, y=dy_tiles, z=int(self.z), walkable=True)


def manhattan(a: Tile, b: Tile) -> int:
    return abs(int(a.x) - int(b.x)) + abs(int(a.y) - int(b.y)) + abs(int(a.z) - int(b.z))


def expected_direction_progress(direction: str, before: Tile, after: Tile) -> bool:
    if direction == 'left':
        return after.x < before.x
    if direction == 'right':
        return after.x > before.x
    if direction == 'up':
        return after.y < before.y
    if direction == 'down':
        return after.y > before.y
    return False


def semantic_progress_ok(*, direction: str, before: Tile, after: Tile, waypoint: Optional[Tile]) -> bool:
    if before == after:
        return False
    if int(before.z) != int(after.z):
        return False
    # A single IntentMove must correspond to a single-tile step.
    if manhattan(before, after) != 1:
        return False
    if not expected_direction_progress(direction, before, after):
        return False
    if waypoint is None:
        return True
    return manhattan(after, waypoint) < manhattan(before, waypoint)
