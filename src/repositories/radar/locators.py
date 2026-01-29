from typing import Union

import cv2

from src.repositories.radar.config import floorsLevelsImgsHashes, images
from src.shared.typings import BBox, GrayImage
from src.utils.core import cacheObjectPosition, hashit, locate


def _validate_floor_level_near_tools(screenshot: GrayImage, radarToolsPosition: BBox) -> bool:
    left, top, width, height = radarToolsPosition
    left = left + width + 8
    top = top - 7
    height = 67
    width = 2
    if top < 0 or left < 0:
        return False
    if (top + height) > screenshot.shape[0] or (left + width) > screenshot.shape[1]:
        return False
    floorLevelImg = screenshot[top:top + height, left:left + width]
    return hashit(floorLevelImg) in floorsLevelsImgsHashes


def _locate_radar_tools_fallback(screenshot: GrayImage, template: GrayImage) -> Union[BBox, None]:
    # Small, guarded fallback for cases where stream/capture introduces slight scaling/blur.
    # Safety: only accept candidates that also validate the floor-level strip next to it.
    base_h, base_w = template.shape[:2]
    screenshot_h, screenshot_w = screenshot.shape[:2]

    # Search a tight scale band; keep it cheap.
    for scale in (0.9, 0.95, 1.0, 1.05, 1.1):
        w = int(round(base_w * scale))
        h = int(round(base_h * scale))
        if w < 4 or h < 4:
            continue
        if w >= screenshot_w or h >= screenshot_h:
            continue

        resized = cv2.resize(template, (w, h), interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)
        match = cv2.matchTemplate(screenshot, resized, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(match)

        # Lower than the default locate() threshold, but validated by floor-level hash.
        if max_val < 0.78:
            continue

        candidate = (max_loc[0], max_loc[1], w, h)
        if _validate_floor_level_near_tools(screenshot, candidate):
            return candidate
    return None


# TODO: add unit tests
# TODO: add perf
@cacheObjectPosition
def getRadarToolsPosition(screenshot: GrayImage) -> Union[BBox, None]:
    found = locate(screenshot, images['tools'])
    if found is not None:
        return found
    return _locate_radar_tools_fallback(screenshot, images['tools'])
