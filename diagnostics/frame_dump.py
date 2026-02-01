from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from contracts.capture import Frame


def dump_enabled() -> bool:
    raw = os.environ.get('FRBOT_DUMP_FRAMES', '')
    if raw is None:
        return False
    return raw.strip().lower() not in {'', '0', 'false', 'no', 'off'}


def _ts() -> str:
    return datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')


def _safe_reason(reason: str) -> str:
    # Keep filenames stable and filesystem-safe.
    s = (reason or 'unknown').strip().lower()
    s = re.sub(r'[^a-z0-9._-]+', '_', s)
    s = re.sub(r'_+', '_', s).strip('_')
    return s[:80] if s else 'unknown'


def dump_frame_ppm(frame: Frame, path: Path) -> bool:
    try:
        if int(frame.width) <= 0 or int(frame.height) <= 0:
            return False
        expected = int(frame.width) * int(frame.height) * 3
        if len(frame.rgb) != expected:
            return False

        path.parent.mkdir(parents=True, exist_ok=True)
        header = f"P6\n{int(frame.width)} {int(frame.height)}\n255\n".encode('ascii')
        path.write_bytes(header + bytes(frame.rgb))
        return True
    except Exception:
        return False


def dump_pair(
    *,
    gate: str,
    before: Frame | None,
    after: Frame | None,
    reason: str,
    out_dir: str | Path = 'diagnostics/frames',
) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    g = (gate or 'gate').strip().lower()
    stamp = _ts()
    r = _safe_reason(reason)

    if before is not None:
        dump_frame_ppm(before, out / f'{g}_{stamp}_{r}_before.ppm')
        if bool(getattr(before, 'minimap_detected', False)) and getattr(before, 'minimap_rgb', b''):
            mm = Frame(
                width=int(before.minimap_width),
                height=int(before.minimap_height),
                monotonic_ts_ns=0,
                digest_hex='',
                rgb=bytes(before.minimap_rgb),
            )
            dump_frame_ppm(mm, out / f'{g}_{stamp}_{r}_before_minimap.ppm')

    if after is not None:
        dump_frame_ppm(after, out / f'{g}_{stamp}_{r}_after.ppm')
        if bool(getattr(after, 'minimap_detected', False)) and getattr(after, 'minimap_rgb', b''):
            mm = Frame(
                width=int(after.minimap_width),
                height=int(after.minimap_height),
                monotonic_ts_ns=0,
                digest_hex='',
                rgb=bytes(after.minimap_rgb),
            )
            dump_frame_ppm(mm, out / f'{g}_{stamp}_{r}_after_minimap.ppm')
