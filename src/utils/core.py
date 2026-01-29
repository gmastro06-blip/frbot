import cv2
try:
    import dxcam
except Exception:  # pragma: no cover
    dxcam = None
import hashlib
import os
import sys

try:
    from farmhash import FarmHash64
except Exception:  # pragma: no cover
    FarmHash64 = None
import numpy as np
from typing import Callable, Union
from src.shared.typings import BBox, GrayImage


def _is_test_mode() -> bool:
    return (
        os.environ.get('PYTEST_CURRENT_TEST') is not None
        or 'pytest' in sys.modules
        or os.environ.get('FENRIL_TEST_MODE') == '1'
        or os.environ.get('FENRIL_SKIP_CAPTURE') == '1'
    )


camera = None
latestScreenshot = None


# TODO: add unit tests
def cacheObjectPosition(func: Callable) -> Callable:
    lastX = None
    lastY = None
    lastW = None
    lastH = None
    lastImgHash = None

    def inner(screenshot):
        nonlocal lastX, lastY, lastW, lastH, lastImgHash
        if lastX != None and lastY != None and lastW != None and lastH != None:
            if hashit(screenshot[lastY:lastY + lastH, lastX:lastX + lastW]) == lastImgHash:
                return (lastX, lastY, lastW, lastH)
        res = func(screenshot)
        if res is None:
            return None
        lastX = res[0]
        lastY = res[1]
        lastW = res[2]
        lastH = res[3]
        lastImgHash = hashit(
            screenshot[lastY:lastY + lastH, lastX:lastX + lastW])
        return res
    return inner


# TODO: add unit tests
def hashit(arr: np.ndarray) -> int:
    contiguous = np.ascontiguousarray(arr)
    if FarmHash64 is not None:
        return FarmHash64(contiguous)
    # Fallback for environments where the farmhash extension is unavailable
    # (e.g. missing MSVC build tools). Produces a stable 64-bit integer.
    digest = hashlib.blake2b(contiguous.tobytes(), digest_size=8).digest()
    return int.from_bytes(digest, byteorder='little', signed=False)


# TODO: add unit tests
def locate(compareImage: GrayImage, img: GrayImage, confidence: float = 0.85, type = cv2.TM_CCOEFF_NORMED) -> Union[BBox, None]:
    match = cv2.matchTemplate(compareImage, img, type)
    res = cv2.minMaxLoc(match)
    if res[1] <= confidence:
        return None
    return res[3][0], res[3][1], len(img[0]), len(img)


# TODO: add unit tests
def locateMultiple(compareImg: GrayImage, img: GrayImage, confidence: float = 0.85) -> Union[BBox, None]:
    match = cv2.matchTemplate(compareImg, img, cv2.TM_CCOEFF_NORMED)
    loc = np.where(match >= confidence)
    resultList = []
    for pt in zip(*loc[::-1]):
        resultList.append((pt[0], pt[1], len(compareImg[0]), len(compareImg)))
    return resultList


# TODO: add unit tests
def getScreenshot() -> GrayImage:
    global camera, latestScreenshot
    if _is_test_mode():
        if latestScreenshot is None:
            latestScreenshot = np.zeros((1, 1), dtype=np.uint8)
        return latestScreenshot

    if camera is None:
        if dxcam is None:
            raise RuntimeError('dxcam is not available; cannot capture screenshots')
        camera = dxcam.create(device_idx=0, output_idx=1, output_color='BGRA')

    screenshot = camera.grab()
    if screenshot is None:
        return latestScreenshot
    latestScreenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2GRAY)
    return latestScreenshot
