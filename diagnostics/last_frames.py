from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from contracts.capture import Frame


@dataclass(slots=True)
class _GateFrames:
    before: Optional[Frame] = None
    after: Optional[Frame] = None


_FRAMES: Dict[str, _GateFrames] = {}


def record_before(gate: str, frame: Frame) -> None:
    g = str(gate or '').strip().lower() or 'unknown'
    bucket = _FRAMES.setdefault(g, _GateFrames())
    bucket.before = frame


def record_after(gate: str, frame: Frame) -> None:
    g = str(gate or '').strip().lower() or 'unknown'
    bucket = _FRAMES.setdefault(g, _GateFrames())
    bucket.after = frame


def snapshot(gate: str) -> Tuple[Optional[Frame], Optional[Frame]]:
    g = str(gate or '').strip().lower() or 'unknown'
    bucket = _FRAMES.get(g)
    if bucket is None:
        return None, None
    return bucket.before, bucket.after


def clear(gate: str | None = None) -> None:
    if gate is None:
        _FRAMES.clear()
        return
    g = str(gate or '').strip().lower() or 'unknown'
    _FRAMES.pop(g, None)
