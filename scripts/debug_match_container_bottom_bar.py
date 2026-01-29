import cv2
from pathlib import Path

from src.utils.image import loadFromRGBToGray
from src.repositories.battleList.config import images


def main() -> None:
    cur = Path('tests/unit/repositories/battleList/locators/getContainerBottomBarPosition')
    screenshot = loadFromRGBToGray(str(cur / 'screenshot.png'))
    template = images['containers']['bottomBar']

    print('screenshot', screenshot.shape, screenshot.dtype)
    print('template', template.shape, template.dtype)

    methods = {
        'CCOEFF_NORMED': cv2.TM_CCOEFF_NORMED,
        'CCORR_NORMED': cv2.TM_CCORR_NORMED,
        'SQDIFF_NORMED': cv2.TM_SQDIFF_NORMED,
    }

    for name, method in methods.items():
        match = cv2.matchTemplate(screenshot, template, method)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(match)
        if method in (cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED):
            best = min_loc
            score = min_val
        else:
            best = max_loc
            score = max_val
        print(name, 'best', best, 'score', score, 'minLoc', min_loc, 'maxLoc', max_loc)


if __name__ == '__main__':
    main()
