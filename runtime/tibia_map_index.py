from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
from typing import Optional, Tuple
from urllib.request import urlretrieve

from PIL import Image

from runtime.map_index import MapIndex


LOG = logging.getLogger(__name__)


class TibiaMapIndex(MapIndex):
    """MapIndex adapter that downloads tibia-map-data assets and answers
    walkability / snapping queries using the repository's `path` images.

    Behavior:
    - Downloads `bounds.json` and `floor-XX-path.png` on demand and caches them
      under `cache_dir`.
    - Converts world tile coordinates to image pixels using `bounds.json` and
      samples the grayscale value from the `-path.png` image to decide
      walkability (non-black -> walkable).
    - Provides a nearest-walkable search when the queried tile is non-walkable.

    This is a pragmatic, self-contained implementation that works offline
    after the first download. If downloads fail it falls back to permissive
    behavior (superclass methods).
    """

    RAW_BASE = "https://raw.githubusercontent.com/tibiamaps/tibia-map-data/main/data"

    def __init__(self, cache_dir: Optional[str] = None, repo_base: Optional[str] = None):
        self.cache_dir = Path(cache_dir or Path.home() / ".frbot" / "tibia_map_data")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.repo_base = repo_base or self.RAW_BASE
        self._bounds = None
        self._images: dict[int, Image.Image] = {}
        self._load_bounds()

    def _download(self, remote: str, local: Path) -> None:
        try:
            LOG.debug("Downloading %s -> %s", remote, local)
            urlretrieve(remote, str(local))
        except Exception:
            LOG.exception("Failed downloading %s", remote)

    def _load_bounds(self) -> None:
        bounds_file = self.cache_dir / "bounds.json"
        remote = f"{self.repo_base}/bounds.json"
        if not bounds_file.exists():
            self._download(remote, bounds_file)
        try:
            with bounds_file.open("r", encoding="utf-8") as fh:
                self._bounds = json.load(fh)
        except Exception:
            LOG.exception("Failed to load bounds.json; map index will be permissive")
            self._bounds = None

    def _ensure_floor_image(self, z: int) -> Optional[Image.Image]:
        if z in self._images:
            return self._images[z]
        fname = f"floor-{z:02d}-path.png"
        local = self.cache_dir / fname
        remote = f"{self.repo_base}/{fname}"
        if not local.exists():
            self._download(remote, local)
        try:
            img = Image.open(local).convert("L")
            self._images[z] = img
            return img
        except Exception:
            LOG.exception("Failed to open floor image %s", local)
            return None

    def _world_to_pixel(self, x: int, y: int) -> Optional[Tuple[int, int]]:
        if not self._bounds:
            return None
        x_min = int(self._bounds["xMin"])
        x_max = int(self._bounds["xMax"])
        y_min = int(self._bounds["yMin"])
        y_max = int(self._bounds["yMax"])
        width = int(self._bounds["width"])
        height = int(self._bounds["height"])
        x_range = x_max - x_min
        y_range = y_max - y_min
        if x_range <= 0 or y_range <= 0:
            return None
        sx = width / x_range
        sy = height / y_range
        px = int(round((x - x_min) * sx))
        py = int(round((y - y_min) * sy))
        return px, py

    def _pixel_to_world(self, px: int, py: int) -> Optional[Tuple[int, int]]:
        if not self._bounds:
            return None
        x_min = int(self._bounds["xMin"])
        x_max = int(self._bounds["xMax"])
        y_min = int(self._bounds["yMin"])
        y_max = int(self._bounds["yMax"])
        width = int(self._bounds["width"])
        height = int(self._bounds["height"])
        x_range = x_max - x_min
        y_range = y_max - y_min
        if x_range <= 0 or y_range <= 0:
            return None
        sx = width / x_range
        sy = height / y_range
        wx = int(round(px / sx)) + x_min
        wy = int(round(py / sy)) + y_min
        return wx, wy

    def is_walkable(self, x: int, y: int, z: int) -> bool:
        img = self._ensure_floor_image(z)
        if img is None or self._bounds is None:
            return True
        coord = self._world_to_pixel(x, y)
        if coord is None:
            return True
        px, py = coord
        if px < 0 or py < 0 or px >= img.width or py >= img.height:
            return False
        try:
            v = img.getpixel((px, py))
            # path images: non-black (threshold) considered walkable
            return int(v) > 16
        except Exception:
            LOG.exception("Error sampling image pixel %s,%s", px, py)
            return True

    def find_nearest_walkable(self, x: int, y: int, z: int, max_radius: int = 4) -> Optional[Tuple[int, int, int]]:
        img = self._ensure_floor_image(z)
        if img is None or self._bounds is None:
            return None
        coord = self._world_to_pixel(x, y)
        if coord is None:
            return None
        px0, py0 = coord
        max_px_radius = max(1, int(math.ceil(max_radius * (img.width / max(1, (int(self._bounds['xMax']) - int(self._bounds['xMin'])))))))

        for r in range(0, max_px_radius + 1):
            # iterate ring
            for dx in range(-r, r + 1):
                for dy in (-r, r):
                    px = px0 + dx
                    py = py0 + dy
                    if 0 <= px < img.width and 0 <= py < img.height:
                        try:
                            if img.getpixel((px, py)) > 16:
                                world = self._pixel_to_world(px, py)
                                if world:
                                    wx, wy = world
                                    return wx, wy, int(z)
                        except Exception:
                            continue
            for dy in range(-r + 1, r):
                for dx in (-r, r):
                    px = px0 + dx
                    py = py0 + dy
                    if 0 <= px < img.width and 0 <= py < img.height:
                        try:
                            if img.getpixel((px, py)) > 16:
                                world = self._pixel_to_world(px, py)
                                if world:
                                    wx, wy = world
                                    return wx, wy, int(z)
                        except Exception:
                            continue
        return None

    def snap_tile(self, x: int, y: int, z: int) -> Tuple[int, int, int]:
        if self.is_walkable(x, y, z):
            return int(x), int(y), int(z)
        found = self.find_nearest_walkable(x, y, z, max_radius=6)
        if found:
            return found
        return int(x), int(y), int(z)
