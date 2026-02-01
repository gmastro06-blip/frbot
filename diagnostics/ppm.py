from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PpmImage:
    width: int
    height: int
    rgb: bytes


def _read_tokens(data: bytes) -> list[bytes]:
    # PPM P6 header tokens, skipping comments.
    tokens: list[bytes] = []
    i = 0
    n = len(data)

    def is_ws(b: int) -> bool:
        return b in (9, 10, 13, 32)

    while i < n and len(tokens) < 4:
        # skip whitespace
        while i < n and is_ws(data[i]):
            i += 1
        if i >= n:
            break
        # comment
        if data[i] == ord('#'):
            while i < n and data[i] not in (10, 13):
                i += 1
            continue

        # token
        j = i
        while j < n and (not is_ws(data[j])):
            if data[j] == ord('#'):
                break
            j += 1
        tok = data[i:j]
        if tok:
            tokens.append(tok)
        i = j

    return tokens


def read_ppm(path: str | Path) -> PpmImage:
    p = Path(path)
    data = p.read_bytes()
    tokens = _read_tokens(data)
    if len(tokens) < 4:
        raise ValueError('ppm header incomplete')

    magic = tokens[0]
    if magic != b'P6':
        raise ValueError('ppm must be P6')

    try:
        w = int(tokens[1])
        h = int(tokens[2])
        maxv = int(tokens[3])
    except Exception as exc:
        raise ValueError(f'ppm header parse error: {exc}') from exc

    if w <= 0 or h <= 0:
        raise ValueError('ppm invalid dimensions')
    if maxv != 255:
        raise ValueError('ppm only supports maxval=255')

    # Find start of binary payload: the byte after the 4th token's trailing whitespace.
    # We re-scan to locate the end of header, including comments.
    i = 0
    n = len(data)
    tok_count = 0

    def is_ws(b: int) -> bool:
        return b in (9, 10, 13, 32)

    while i < n and tok_count < 4:
        while i < n and is_ws(data[i]):
            i += 1
        if i >= n:
            break
        if data[i] == ord('#'):
            while i < n and data[i] not in (10, 13):
                i += 1
            continue
        while i < n and (not is_ws(data[i])):
            i += 1
        tok_count += 1

    while i < n and is_ws(data[i]):
        i += 1

    expected = w * h * 3
    payload = data[i : i + expected]
    if len(payload) != expected:
        raise ValueError('ppm payload truncated')

    return PpmImage(width=w, height=h, rgb=payload)
