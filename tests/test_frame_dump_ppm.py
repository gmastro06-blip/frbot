from __future__ import annotations

from pathlib import Path

from contracts.capture import Frame
from diagnostics.frame_dump import dump_frame_ppm


def test_dump_frame_ppm_writes_valid_header(tmp_path: Path) -> None:
    w, h = 2, 2
    rgb = bytes([
        255, 0, 0,
        0, 255, 0,
        0, 0, 255,
        255, 255, 255,
    ])
    f = Frame(width=w, height=h, monotonic_ts_ns=0, digest_hex='', rgb=rgb)

    out = tmp_path / 'frame.ppm'
    ok = dump_frame_ppm(f, path=out)
    assert ok is True

    data = out.read_bytes()
    assert data.startswith(b'P6\n2 2\n255\n')
    assert data.endswith(rgb)
