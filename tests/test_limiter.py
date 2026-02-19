import time

from cavebot_entrypoint import Limiter


def test_limiter_basic():
    lim = Limiter(10.0)  # 10 Hz -> 0.1s period
    # First ready should be True (starts ready)
    assert lim.ready() is True
    # Immediately calling again should be False
    assert lim.ready() is False
    # After waiting longer than period it should be ready again
    time.sleep(0.12)
    assert lim.ready() is True
