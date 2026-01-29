from typing import Union
from src.shared.typings import BBox, GrayImage
from src.utils.core import cacheObjectPosition, locate
from .config import images


# PERF: [0.05150189999999988, 2.000000000279556e-06]
@cacheObjectPosition
def getBattleListIconPosition(screenshot: GrayImage) -> Union[BBox, None]:
    return locate(screenshot, images['icons']['ng_battleList'])


# PERF: [0.05364349999999973, 1.8999999991109462e-06]
@cacheObjectPosition
def getContainerBottomBarPosition(screenshot: GrayImage) -> Union[BBox, None]:
    # On full-screen screenshots there can be multiple perfect matches for this
    # tiny 4px-high template. The real battle list container lives in the
    # upper-right area, so constrain the search region to avoid false positives.
    height = len(screenshot)
    width = len(screenshot[0]) if height else 0

    if height >= 900 and width >= 1600:
        x0 = int(width * 0.7)
        y1 = int(height * 0.75)
        roi = screenshot[0:y1, x0:width]
        res = locate(roi, images['containers']['bottomBar'])
        if res is None:
            return None
        return (res[0] + x0, res[1], res[2], res[3])

    return locate(screenshot, images['containers']['bottomBar'])
