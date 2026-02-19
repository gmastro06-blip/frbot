from runtime.cavebot_runner import _marker_reacquire_every


def test_marker_reacquire_default():
    # Default should be an int >= 1
    v = _marker_reacquire_every()
    assert isinstance(v, int)
    assert v >= 1
