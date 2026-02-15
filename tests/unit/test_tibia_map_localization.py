from __future__ import annotations

import json

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from contracts.errors import PreflightFailed
from runtime.minimap_localization import localize_minimap
from runtime.tibia_map_data import load_tibia_map_dataset, require_tibia_map_data_dir


def _write_png(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb.astype(np.uint8)).save(path)


def _make_dataset(tmp_path: Path) -> Path:
    root = tmp_path / "mapdata"
    root.mkdir(parents=True, exist_ok=True)

    bounds = {
        "xMin": 32000,
        "xMax": 32095,
        "yMin": 31000,
        "yMax": 31095,
        "zMin": 7,
        "zMax": 7,
        "width": 96,
        "height": 96,
        "floorIDs": ["07"],
    }
    (root / "bounds.json").write_text(json.dumps(bounds), encoding="utf-8")

    base = np.zeros((96, 96, 3), dtype=np.uint8)
    for y in range(96):
        for x in range(96):
            base[y, x, 0] = (x * 3 + y * 5) % 256
            base[y, x, 1] = (x * 7 + y * 11) % 256
            base[y, x, 2] = (x * 13 + y * 17) % 256

    _write_png(root / "floor-07-map.png", base)
    _write_png(root / "floor-07-path.png", base)
    return root


def test_require_tibia_map_data_dir_missing_env_raises() -> None:
    with pytest.raises(PreflightFailed):
        require_tibia_map_data_dir(env={})


def test_load_tibia_map_dataset_requires_bounds_keys(tmp_path: Path) -> None:
    root = tmp_path / "mapdata"
    root.mkdir(parents=True, exist_ok=True)
    (root / "bounds.json").write_text("{}", encoding="utf-8")

    with pytest.raises(PreflightFailed):
        load_tibia_map_dataset(root=root)


def test_localize_minimap_recovers_absolute_player_position(tmp_path: Path) -> None:
    root = _make_dataset(tmp_path)
    ds = load_tibia_map_dataset(root=root)

    floor = ds.floor_images(7).map_rgb
    patch = floor[20:36, 30:46].copy()

    marker_px = (8, 8)
    patch[marker_px[1], marker_px[0], :] = np.array([255, 0, 255], dtype=np.uint8)

    out = localize_minimap(
        minimap_rgb=patch.tobytes(),
        minimap_width=16,
        minimap_height=16,
        floor_z=7,
        dataset=ds,
        marker_px=marker_px,
        marker_rgb=(255, 0, 255),
        marker_tol=0,
        prev_player_world=None,
    )

    assert out.player_z == 7
    assert out.top_left_x == 32030
    assert out.top_left_y == 31020
    assert out.player_x == 32038
    assert out.player_y == 31028
    assert out.score > 0.95
