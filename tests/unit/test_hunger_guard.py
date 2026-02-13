from __future__ import annotations

from contracts.capture import Frame
from contracts.evidence import Roi
from runtime.hunger_guard import HungerSettings, is_hungry, should_press_eat


def _frame_with_roi_color(*, w: int, h: int, roi: Roi, bg: tuple[int, int, int], fg: tuple[int, int, int], fg_ratio: float) -> Frame:
    rgb = bytearray([int(bg[0]), int(bg[1]), int(bg[2])] * (int(w) * int(h)))

    total = int(roi.width) * int(roi.height)
    hits = max(0, min(total, int(round(float(total) * float(fg_ratio)))))

    k = 0
    for yy in range(int(roi.y), int(roi.y) + int(roi.height)):
        for xx in range(int(roi.x), int(roi.x) + int(roi.width)):
            if k >= hits:
                break
            i = (yy * int(w) + xx) * 3
            rgb[i + 0] = int(fg[0])
            rgb[i + 1] = int(fg[1])
            rgb[i + 2] = int(fg[2])
            k += 1
        if k >= hits:
            break

    return Frame(
        width=int(w),
        height=int(h),
        monotonic_ts_ns=0,
        digest_hex='x',
        rgb=bytes(rgb),
    )


def test_is_hungry_true_when_ratio_above_threshold() -> None:
    roi = Roi(name='hunger_status', x=2, y=2, width=10, height=10)
    settings = HungerSettings(hungry_rgb=(255, 170, 0), color_tol=10, match_ratio_min=0.08)
    frame = _frame_with_roi_color(w=20, h=20, roi=roi, bg=(0, 0, 0), fg=(255, 170, 0), fg_ratio=0.20)

    hungry, ratio = is_hungry(frame, roi, settings)

    assert hungry is True
    assert ratio >= 0.19


def test_is_hungry_false_when_ratio_below_threshold() -> None:
    roi = Roi(name='hunger_status', x=1, y=1, width=10, height=10)
    settings = HungerSettings(hungry_rgb=(255, 170, 0), color_tol=10, match_ratio_min=0.10)
    frame = _frame_with_roi_color(w=16, h=16, roi=roi, bg=(0, 0, 0), fg=(255, 170, 0), fg_ratio=0.03)

    hungry, ratio = is_hungry(frame, roi, settings)

    assert hungry is False
    assert ratio <= 0.04


def test_should_press_eat_respects_cooldown() -> None:
    assert should_press_eat(hungry=True, now_ms=1000, last_eat_ts_ms=None, eat_interval_ms=1200) is True
    assert should_press_eat(hungry=True, now_ms=1500, last_eat_ts_ms=1000, eat_interval_ms=1200) is False
    assert should_press_eat(hungry=True, now_ms=2200, last_eat_ts_ms=1000, eat_interval_ms=1200) is True
    assert should_press_eat(hungry=False, now_ms=5000, last_eat_ts_ms=1000, eat_interval_ms=1200) is False
