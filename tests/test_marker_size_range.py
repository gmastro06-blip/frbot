from __future__ import annotations

from contracts.capture import Frame
from runtime.minimap_semantics import detect_player_marker, marker_config_from_env


def test_marker_rejects_cluster_too_large() -> None:
    # 10x10 all-magenta minimap => 100 pixels in one cluster.
    w = 10
    h = 10
    rgb = bytes([255, 0, 255] * (w * h))
    frame = Frame(
        width=w,
        height=h,
        monotonic_ts_ns=0,
        digest_hex='',
        rgb=b'',
        minimap_detected=True,
        minimap_rgb=rgb,
        minimap_width=w,
        minimap_height=h,
        minimap_digest_hex='',
    )

    cfg = marker_config_from_env(
        '255,0,255',
        '0',
        '5',
        '20',  # max_pixels
        '0.0',
        '50.0',
    )

    assert detect_player_marker(frame, cfg) is None
