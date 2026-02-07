from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as a script without installing the package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.windows import win32 as w32


def _safe_print(line: str) -> None:
    # Avoid Windows console encoding failures (e.g. cp1252) on odd window titles.
    try:
        sys.stdout.buffer.write((str(line) + '\n').encode('utf-8', errors='backslashreplace'))
    except Exception:
        try:
            print(str(line).encode('utf-8', errors='backslashreplace').decode('utf-8'))
        except Exception:
            print(str(line))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='List top-level visible windows (Win32).')
    ap.add_argument('--filter', default='', help='Case-insensitive substring filter for window titles')
    ap.add_argument('--json', action='store_true', help='Emit JSON array instead of text lines')
    args = ap.parse_args(argv)

    flt = str(args.filter or '')
    wins = w32.list_top_level_windows(title_substring=flt, visible_only=True)
    fg = 0
    try:
        fg = int(w32.get_foreground_window())
    except Exception:
        fg = 0

    rows: list[dict[str, object]] = []
    for w in wins:
        rows.append(
            {
                'hwnd_hex': hex(int(w.hwnd)),
                'hwnd': int(w.hwnd),
                'foreground': bool(int(w.hwnd) == fg),
                'title': str(w.title or ''),
                'pid': int(w.pid),
                'visible': bool(w.visible),
                'minimized': bool(w.minimized),
            }
        )

    if args.json:
        # JSON may still contain unicode; write as UTF-8 safely.
        _safe_print(json.dumps({'foreground_hwnd_hex': hex(int(fg)), 'windows': rows}, ensure_ascii=False))
        return 0

    _safe_print(f'Foreground HWND: {hex(int(fg))}')
    for r in rows:
        mark = '*' if r['foreground'] else ' '
        _safe_print(f"{mark} {r['hwnd_hex']}  pid={r['pid']}  minimized={r['minimized']}  title={r['title']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
