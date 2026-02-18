from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any

import cv2
import numpy as np

from contracts.errors import PreflightFailed
from runtime.tibia_map_data import TibiaMapDataset


@dataclass(frozen=True, slots=True)
class LocalizationResult:
    floor_z: int
    top_left_x: int
    top_left_y: int
    player_x: int
    player_y: int
    player_z: int
    score: float
    ambiguous: bool


def _as_gray(img_rgb: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    return cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)


def _mask_player_marker(minimap_rgb: np.ndarray[Any, Any], *, marker_rgb: tuple[int, int, int], marker_tol: int) -> np.ndarray[Any, Any]:
    marker = np.array([int(marker_rgb[0]), int(marker_rgb[1]), int(marker_rgb[2])], dtype=np.int16)
    src = minimap_rgb.astype(np.int16)
    dist = np.abs(src - marker)
    keep = np.all(dist <= int(marker_tol), axis=2)
    mask = np.full(minimap_rgb.shape[:2], 255, dtype=np.uint8)
    mask[keep] = 0
    return mask


def localize_minimap(
    *,
    minimap_rgb: bytes,
    minimap_width: int,
    minimap_height: int,
    floor_z: int,
    dataset: TibiaMapDataset,
    marker_px: Optional[tuple[int, int]] = None,
    marker_rgb: tuple[int, int, int] = (255, 0, 255),
    marker_tol: int = 30,
    prev_player_world: Optional[tuple[int, int]] = None,
    search_radius_px: int = 512,
    ambiguity_margin: float = 0.01,
) -> LocalizationResult:
    w = int(minimap_width)
    h = int(minimap_height)
    if w <= 4 or h <= 4:
        raise PreflightFailed("waypoints_localize_invalid_minimap_size")
    if len(minimap_rgb) != (w * h * 3):
        raise PreflightFailed("waypoints_localize_invalid_minimap_buffer")

    floor = dataset.floor_images(int(floor_z)).map_rgb
    fh, fw = floor.shape[:2]
    if h > fh or w > fw:
        raise PreflightFailed("waypoints_localize_minimap_larger_than_floor")

    mini = np.frombuffer(minimap_rgb, dtype=np.uint8).reshape((h, w, 3))
    mini_gray = _as_gray(mini)
    floor_gray = _as_gray(floor)

    mask = _mask_player_marker(mini, marker_rgb=marker_rgb, marker_tol=max(0, int(marker_tol)))
    use_mask = bool(int(np.count_nonzero(mask)) >= int(mask.size * 0.80))

    x0 = 0
    y0 = 0
    x1 = int(fw)
    y1 = int(fh)
    if prev_player_world is not None:
        marker_x = int(w // 2 if marker_px is None else marker_px[0])
        marker_y = int(h // 2 if marker_px is None else marker_px[1])
        guess_top_left_x = int(prev_player_world[0]) - int(dataset.bounds.x_min) - int(marker_x)
        guess_top_left_y = int(prev_player_world[1]) - int(dataset.bounds.y_min) - int(marker_y)
        radius = max(64, int(search_radius_px))

        x0 = max(0, int(guess_top_left_x - radius))
        y0 = max(0, int(guess_top_left_y - radius))
        x1 = min(int(fw), int(guess_top_left_x + radius + w))
        y1 = min(int(fh), int(guess_top_left_y + radius + h))

    hay = floor_gray[y0:y1, x0:x1]
    if hay.shape[0] < h or hay.shape[1] < w:
        raise PreflightFailed("waypoints_localize_search_window_too_small")

    method = cv2.TM_CCOEFF_NORMED
    if use_mask:
        match = cv2.matchTemplate(hay, mini_gray, method, mask=mask)
    else:
        match = cv2.matchTemplate(hay, mini_gray, method)

    min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(match)
    top_left_local = (int(max_loc[0]), int(max_loc[1]))
    top_left_x = int(x0 + top_left_local[0])
    top_left_y = int(y0 + top_left_local[1])

    second_score = float(min_val)
    if match.size >= 2:
        tmp = match.copy()
        tmp[top_left_local[1], top_left_local[0]] = -2.0
        _m2, max2, _l2, _p2 = cv2.minMaxLoc(tmp)
        second_score = float(max2)

    marker_x = int(w // 2 if marker_px is None else marker_px[0])
    marker_y = int(h // 2 if marker_px is None else marker_px[1])
    player_x = int(dataset.bounds.x_min + top_left_x + marker_x)
    player_y = int(dataset.bounds.y_min + top_left_y + marker_y)
    ambiguous = bool((float(max_val) - float(second_score)) <= float(max(0.0, ambiguity_margin)))

    return LocalizationResult(
        floor_z=int(floor_z),
        top_left_x=int(dataset.bounds.x_min + top_left_x),
        top_left_y=int(dataset.bounds.y_min + top_left_y),
        player_x=int(player_x),
        player_y=int(player_y),
        player_z=int(floor_z),
        score=float(max_val),
        ambiguous=bool(ambiguous),
    )
