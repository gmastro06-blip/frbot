from __future__ import annotations

from statistics import median
from typing import Iterable, Tuple, List


def median_mad(values: Iterable[float]) -> Tuple[float, float]:
    xs = sorted([float(x) for x in values])
    if not xs:
        raise ValueError('no values')
    m = median(xs)
    devs = [abs(x - m) for x in xs]
    mad = median(devs)
    return m, mad


def ewma(values: Iterable[float], alpha: float = 0.3, initial: float | None = None) -> float:
    it = iter(values)
    if initial is None:
        try:
            s = float(next(it))
        except StopIteration:
            raise ValueError('no values')
    else:
        s = float(initial)
    for v in it:
        s = alpha * float(v) + (1 - alpha) * s
    return s
