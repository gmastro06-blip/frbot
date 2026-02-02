from __future__ import annotations

import sys
import json
import os
import argparse
import time
from pathlib import Path


# Allow running as a script without installing the package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.capture.meld_real import MeldBoundWindowRealCapture, _sample_luma_stats
from adapters.window.win32 import Win32WindowBinding
from adapters.windows.win32 import (
    find_window_by_title_substring,
    get_foreground_window,
    get_window_text,
    is_window_minimized,
    is_window_visible,
    list_top_level_windows,
    try_focus_window,
)
from contracts.errors import PreflightFailed
from diagnostics.fatal import write_fatal
from diagnostics.frame_dump import dump_frame_ppm
from runtime.runner import _load_config_from_env


def _hard_fail(reason: str, *, details: dict) -> int:
    exc = PreflightFailed(reason)
    setattr(exc, 'details', details)
    write_fatal(reason, exc, details=details)
    print(json.dumps({'ok': False, 'reason': reason, 'details': details}, ensure_ascii=False))
    return 2


def _list_windows(*, title_substring: str = '', visible_only: bool = True, limit: int = 60) -> int:
    try:
        fg = int(get_foreground_window())
        fg_title = get_window_text(fg)
    except Exception:
        fg = 0
        fg_title = ''

    wins = list_top_level_windows(title_substring=title_substring, visible_only=visible_only)
    wins = wins[: max(0, int(limit))]

    payload = {
        'ok': True,
        'mode': 'list_windows',
        'filter': {'title_substring': title_substring, 'visible_only': bool(visible_only), 'limit': int(limit)},
        'foreground': {'hwnd': hex(fg) if fg else None, 'title': fg_title},
        'windows': [
            {
                'hwnd': hex(int(w.hwnd)),
                'title': w.title,
                'pid': int(w.pid),
                'visible': bool(w.visible),
                'minimized': bool(w.minimized),
            }
            for w in wins
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('--list-windows', action='store_true', help='List top-level windows as JSON and exit')
    ap.add_argument('--filter', default='', help='Filter windows by title substring (case-insensitive)')
    ap.add_argument('--all', action='store_true', help='Include invisible windows in listing')
    ap.add_argument('--limit', type=int, default=60, help='Max number of windows to return')
    ap.add_argument('--focus', action='store_true', help='Best-effort focus the bound window before verification')
    ap.add_argument('--wait-seconds', type=float, default=0.0, help='Wait up to N seconds for the target window to become foreground')
    args = ap.parse_args()

    if args.list_windows:
        return _list_windows(title_substring=str(args.filter or ''), visible_only=not bool(args.all), limit=int(args.limit))

    cfg = _load_config_from_env()

    backend = (os.environ.get('FRBOT_CAPTURE_BACKEND', 'meld') or 'meld').strip().lower()
    if backend != 'meld':
        return _hard_fail('capture_black_or_unavailable', details={'expected_backend': 'meld', 'got': backend})

    binding = Win32WindowBinding(hwnd=int(cfg.window_hwnd), title_substring=cfg.window_title_substring)

    target_hwnd = int(cfg.window_hwnd)
    if target_hwnd <= 0 and (cfg.window_title_substring or '').strip():
        m0 = find_window_by_title_substring(cfg.window_title_substring)
        if m0 is not None:
            target_hwnd = int(m0.hwnd)

    if args.focus:
        # Best-effort focus attempt to reduce friction; binding is still strict.
        if target_hwnd > 0:
            try_focus_window(target_hwnd)
            time.sleep(0.15)

    if float(args.wait_seconds) > 0 and target_hwnd > 0:
        deadline = time.time() + float(args.wait_seconds)
        while time.time() < deadline:
            try:
                if int(get_foreground_window()) == int(target_hwnd):
                    break
            except Exception:
                pass
            time.sleep(0.2)

    bvr = binding.verify()
    if not bvr.ok:
        # Provide actionable diagnostics for binding setup.
        fg_hwnd = 0
        fg_title = ''
        try:
            fg_hwnd = int(get_foreground_window())
            fg_title = get_window_text(fg_hwnd)
        except Exception:
            pass

        match = None
        if (cfg.window_title_substring or '').strip():
            try:
                m = find_window_by_title_substring(cfg.window_title_substring)
                if m is not None:
                    match = {
                        'hwnd': hex(int(m.hwnd)),
                        'title': m.title,
                        'visible': bool(is_window_visible(int(m.hwnd))),
                        'minimized': bool(is_window_minimized(int(m.hwnd))),
                    }
            except Exception:
                match = None

        return _hard_fail(
            'window_binding_lost',
            details={
                'reason': bvr.reason,
                'config': {
                    'window_hwnd': hex(int(cfg.window_hwnd)) if int(cfg.window_hwnd) else None,
                    'window_title_substring': str(cfg.window_title_substring or ''),
                },
                'foreground': {
                    'hwnd': hex(int(fg_hwnd)) if int(fg_hwnd) else None,
                    'title': fg_title,
                },
                'title_match': match,
                'hint': 'Run: python tools/test_capture_real.py --list-windows --filter tibia',
            },
        )

    try:
        cap = MeldBoundWindowRealCapture(binding=binding)
    except ImportError as exc:
        return _hard_fail('capture_black_or_unavailable', details={'import_error': str(exc)})

    v = cap.verify()
    if not v.ok:
        return _hard_fail('capture_black_detected', details={'verify_reason': v.reason, 'backend': cap.name})

    out_dir = Path('diagnostics') / 'capture_test'
    out_dir.mkdir(parents=True, exist_ok=True)

    frame_sizes: list[list[int]] = []
    mean_luma: list[float] = []
    std_luma: list[float] = []
    is_black: list[bool] = []

    for i in range(3):
        f = cap.grab()
        frame_sizes.append([int(f.width), int(f.height)])

        mean, std, all_zero = _sample_luma_stats(bytes(f.rgb), width=int(f.width), height=int(f.height))
        mean_luma.append(float(mean))
        std_luma.append(float(std))

        black = bool(all_zero or std <= 5.0)
        is_black.append(bool(black))

        ppm_path = out_dir / f'meld_{i+1}.ppm'
        dump_frame_ppm(f, ppm_path)

        if black:
            return _hard_fail(
                'capture_black_detected',
                details={
                    'backend': cap.name,
                    'frame_index': i + 1,
                    'frame_size': [int(f.width), int(f.height)],
                    'mean_luma': float(mean),
                    'std_luma': float(std),
                    'all_zero': bool(all_zero),
                    'ppm': str(ppm_path.as_posix()),
                },
            )

    payload = {
        'ok': True,
        'backend': cap.name,
        'hwnd': hex(int(cfg.window_hwnd)),
        'frame_sizes': frame_sizes,
        'mean_luma': mean_luma,
        'std_luma': std_luma,
        'is_black': is_black,
    }

    print(json.dumps(payload, ensure_ascii=False))

    # Binary acceptance criteria.
    if any(is_black):
        return 2
    if any(s <= 5.0 for s in std_luma):
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
