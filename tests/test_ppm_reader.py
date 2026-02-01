from __future__ import annotations

from pathlib import Path

import pytest

from diagnostics.ppm import read_ppm


def test_read_ppm_roundtrip(tmp_path: Path) -> None:
    # Minimal valid P6 ppm with 2x1 pixels.
    data = b"P6\n2 1\n255\n" + bytes([255, 0, 0, 0, 255, 0])
    p = tmp_path / 'x.ppm'
    p.write_bytes(data)

    img = read_ppm(p)
    assert img.width == 2
    assert img.height == 1
    assert img.rgb == bytes([255, 0, 0, 0, 255, 0])


def test_read_ppm_rejects_non_p6(tmp_path: Path) -> None:
    p = tmp_path / 'x.ppm'
    p.write_bytes(b"P3\n1 1\n255\n0 0 0\n")
    with pytest.raises(ValueError):
        read_ppm(p)
