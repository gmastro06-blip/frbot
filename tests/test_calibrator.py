from route_opt.calibrator import median_mad, ewma


def test_median_mad():
    m, mad = median_mad([1, 2, 2, 100])
    assert m == 2
    assert mad >= 0


def test_ewma():
    v = [10, 12, 11, 13]
    r = ewma(v, alpha=0.5)
    assert isinstance(r, float)
