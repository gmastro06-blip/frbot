from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import math

import numpy as np

from contracts.capture import Frame


@dataclass(frozen=True, slots=True)
class SuggestedClick:
    x: int
    y: int
    score: float
    kind: str


def _frame_rgb_to_array(frame: Frame) -> np.ndarray | None:
    rgb = getattr(frame, 'rgb', b'')
    w = int(getattr(frame, 'width', 0) or 0)
    h = int(getattr(frame, 'height', 0) or 0)
    if not rgb or w <= 0 or h <= 0:
        return None
    if len(rgb) != (w * h * 3):
        return None
    try:
        arr = np.frombuffer(rgb, dtype=np.uint8)
        return arr.reshape((h, w, 3))
    except Exception:
        return None


def _tile_centers_around(*, player_x: int, player_y: int, tile: int, radius: int) -> Iterable[tuple[int, int]]:
    # Scan centers on a tile grid around the player.
    # This intentionally stays local (short radius) so the output is only a hint.
    max_step = max(1, int(math.ceil(float(radius) / float(tile))))
    for dy in range(-max_step, max_step + 1):
        for dx in range(-max_step, max_step + 1):
            if dx == 0 and dy == 0:
                continue
            cx = int(player_x + dx * tile)
            cy = int(player_y + dy * tile)
            if (cx - player_x) * (cx - player_x) + (cy - player_y) * (cy - player_y) > int(radius) * int(radius):
                continue
            yield cx, cy


def suggest_clickxy_for_corpse(
    *,
    frame: Frame,
    player_pos: tuple[int, int] | None,
    radius_px: int = 96,
    tile_px: int = 32,
    max_suggestions: int = 12,
) -> list[SuggestedClick]:
    """Suggest ClickXY candidates for looting by visual heuristics ONLY.

    Contract:
    - MUST NOT certify success.
    - Output is only ordered candidate click coordinates (frame-space).

    Heuristic (best-effort): score nearby tiles around player by a mix of
    edge density, local contrast, and a weak "corpse-like" color ratio.
    """

    if player_pos is None:
        return []
    px, py = int(player_pos[0]), int(player_pos[1])
    if int(radius_px) <= 0 or int(tile_px) <= 0:
        return []

    arr = _frame_rgb_to_array(frame)
    if arr is None:
        return []
    h, w, _c = arr.shape

    def clamp(v: int, lo: int, hi: int) -> int:
        return lo if v < lo else (hi if v > hi else v)

    half = max(6, int(tile_px) // 2)

    out: list[SuggestedClick] = []
    for cx, cy in _tile_centers_around(player_x=int(px), player_y=int(py), tile=int(tile_px), radius=int(radius_px)):
        x0 = clamp(int(cx) - half, 0, int(w) - 1)
        y0 = clamp(int(cy) - half, 0, int(h) - 1)
        x1 = clamp(int(cx) + half, 0, int(w) - 1)
        y1 = clamp(int(cy) + half, 0, int(h) - 1)
        if x1 <= x0 or y1 <= y0:
            continue

        patch = arr[int(y0) : int(y1) + 1, int(x0) : int(x1) + 1, :]
        if patch.size <= 0:
            continue

        # Grayscale + local contrast.
        p = patch.astype(np.int16)
        r = p[:, :, 0]
        g = p[:, :, 1]
        b = p[:, :, 2]
        gray = (r * 30 + g * 59 + b * 11) // 100

        contrast = float(np.std(gray))  # 0..~75
        mean_luma = float(np.mean(gray))

        # Simple edge density: avg absolute diff in x/y.
        dx = np.abs(gray[:, 1:] - gray[:, :-1])
        dy = np.abs(gray[1:, :] - gray[:-1, :])
        edge = float(np.mean(dx)) + float(np.mean(dy))

        # Weak corpse-ish color ratio: brown/red-ish pixels with mid luma.
        # This is intentionally permissive; it must not be used for success.
        mid = (gray >= 25) & (gray <= 210)
        brownish = (r >= g) & (g >= b) & (r - b >= 12)
        corpse_like_ratio = float(np.mean((mid & brownish).astype(np.float32)))

        # Penalize very bright UI tiles; prefer mid-range (playfield).
        bright_penalty = 0.0
        if mean_luma > 220.0:
            bright_penalty = (mean_luma - 220.0) / 35.0

        # Combine into a score.
        score = (
            (edge / 50.0) * 0.55
            + (contrast / 35.0) * 0.30
            + (corpse_like_ratio) * 0.35
            - bright_penalty * 0.40
        )

        # Reject near-uniform patches.
        if contrast < 4.0 and edge < 10.0:
            continue

        out.append(SuggestedClick(x=int(cx), y=int(cy), score=float(score), kind='visual_tile'))

    out.sort(key=lambda s: float(s.score), reverse=True)
    if int(max_suggestions) <= 0:
        return []
    return out[: int(max_suggestions)]
