from __future__ import annotations

import json
import os

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

import numpy as np
from PIL import Image

from contracts.errors import PreflightFailed


@dataclass(frozen=True)
class TibiaMapBounds:
    x_min: int
    x_max: int
    y_min: int
    y_max: int
    z_min: int
    z_max: int
    width: int
    height: int
    floor_ids: tuple[str, ...]


@dataclass(frozen=True)
class FloorImages:
    map_rgb: np.ndarray[Any, Any]
    path_rgb: np.ndarray[Any, Any]


class TibiaMapDataset:
    def __init__(self, *, root: Path, bounds: TibiaMapBounds) -> None:
        self._root = Path(root)
        self.bounds = bounds
        self._cache: dict[int, FloorImages] = {}

    @property
    def root(self) -> Path:
        return self._root

    def floor_images(self, z: int) -> FloorImages:
        floor = int(z)
        cached = self._cache.get(floor)
        if cached is not None:
            return cached

        floor_id = str(floor).zfill(2)
        map_path = self._root / f"floor-{floor_id}-map.png"
        path_path = self._root / f"floor-{floor_id}-path.png"

        if not map_path.exists():
            raise PreflightFailed(f"tibia_map_data_missing_floor_map:{floor_id}")
        if not path_path.exists():
            raise PreflightFailed(f"tibia_map_data_missing_floor_path:{floor_id}")

        map_rgb = np.asarray(Image.open(map_path).convert("RGB"), dtype=np.uint8)
        path_rgb = np.asarray(Image.open(path_path).convert("RGB"), dtype=np.uint8)

        if map_rgb.shape[:2] != (int(self.bounds.height), int(self.bounds.width)):
            raise PreflightFailed(f"tibia_map_data_floor_map_size_mismatch:{floor_id}")
        if path_rgb.shape[:2] != (int(self.bounds.height), int(self.bounds.width)):
            raise PreflightFailed(f"tibia_map_data_floor_path_size_mismatch:{floor_id}")

        out = FloorImages(map_rgb=map_rgb, path_rgb=path_rgb)
        self._cache[floor] = out
        return out


def require_tibia_map_data_dir(*, env: Optional[dict[str, str]] = None) -> Path:
    source = os.environ if env is None else env
    raw = str(source.get("FRBOT_TIBIA_MAP_DATA_DIR") or "").strip()
    if not raw:
        raise PreflightFailed("tibia_map_data_dir_missing")

    path = Path(raw)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists() or not path.is_dir():
        raise PreflightFailed("tibia_map_data_dir_not_found")
    return path


def _read_required_int(data: dict[str, object], key: str) -> int:
    if key not in data:
        raise PreflightFailed(f"tibia_map_bounds_missing:{key}")
    value = data[key]
    if isinstance(value, bool):
        raise PreflightFailed(f"tibia_map_bounds_invalid:{key}")
    if not isinstance(value, (int, float, str)):
        raise PreflightFailed(f"tibia_map_bounds_invalid:{key}")
    try:
        return int(value)
    except Exception as exc:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            pass
        raise PreflightFailed(f"tibia_map_bounds_invalid:{key}") from exc


def _load_bounds(path: Path) -> TibiaMapBounds:
    if not path.exists():
        raise PreflightFailed("tibia_map_bounds_missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
    except Exception as exc:
        try:
            from runtime.error_policy import should_reraise

            if should_reraise():
                raise
        except Exception:
            pass
        raise PreflightFailed("tibia_map_bounds_invalid_json") from exc

    if not isinstance(raw, dict):
        raise PreflightFailed("tibia_map_bounds_invalid_schema")

    floor_ids_raw = raw.get("floorIDs")
    if not isinstance(floor_ids_raw, list) or not floor_ids_raw:
        raise PreflightFailed("tibia_map_bounds_missing:floorIDs")
    floor_ids = tuple(str(x) for x in floor_ids_raw)

    bounds = TibiaMapBounds(
        x_min=_read_required_int(raw, "xMin"),
        x_max=_read_required_int(raw, "xMax"),
        y_min=_read_required_int(raw, "yMin"),
        y_max=_read_required_int(raw, "yMax"),
        z_min=_read_required_int(raw, "zMin"),
        z_max=_read_required_int(raw, "zMax"),
        width=_read_required_int(raw, "width"),
        height=_read_required_int(raw, "height"),
        floor_ids=floor_ids,
    )

    if int(bounds.width) <= 0 or int(bounds.height) <= 0:
        raise PreflightFailed("tibia_map_bounds_invalid_dimensions")

    return bounds


def load_tibia_map_dataset(*, env: Optional[dict[str, str]] = None, root: Optional[Path] = None) -> TibiaMapDataset:
    base = Path(root) if root is not None else require_tibia_map_data_dir(env=env)
    if not base.is_absolute():
        base = (Path.cwd() / base).resolve()

    bounds = _load_bounds(base / "bounds.json")
    return TibiaMapDataset(root=base, bounds=bounds)
