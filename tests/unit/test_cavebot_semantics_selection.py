from __future__ import annotations

from contracts.capture import Frame
from runtime.cavebot_semantics import select_player_marker


def _build_minimap_with_blobs() -> bytes:
    w = 10
    h = 10
    data = bytearray(w * h * 3)

    def paint_blob(x0: int, y0: int) -> None:
        for yy in range(y0, y0 + 3):
            for xx in range(x0, x0 + 3):
                i = (yy * w + xx) * 3
                data[i] = 0
                data[i + 1] = 200
                data[i + 2] = 0

    paint_blob(0, 0)
    paint_blob(4, 4)
    return bytes(data)


def test_select_player_marker_prefers_center_without_prev_marker() -> None:
    frame = Frame(
        width=0,
        height=0,
        monotonic_ts_ns=1,
        digest_hex="x",
        rgb=b"",
        minimap_detected=True,
        minimap_rgb=_build_minimap_with_blobs(),
        minimap_width=10,
        minimap_height=10,
        minimap_digest_hex="m",
    )

    sel = select_player_marker(
        frame,
        marker_rgb=(0, 200, 0),
        tol=5,
        min_pixels=5,
        max_pixels=0,
        prev_marker=None,
    )

    assert sel.abort_reason is None
    assert sel.marker is not None
    assert int(sel.marker.x_px) == 5
    assert int(sel.marker.y_px) == 5
