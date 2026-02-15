from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Iterable


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_expected(raw: str) -> list[str]:
    items = [p.strip().lower() for p in str(raw or '').split(',') if p.strip()]
    return items


def _map_phrase(phrase: str, *, map_enhanced_to_blessing: bool) -> str:
    p = str(phrase or '').strip().lower()
    if map_enhanced_to_blessing and p == 'enhanced':
        return 'blessing'
    return p


def _candidate_trace_paths(root: Path) -> list[Path]:
    fixed = [
        root / 'diagnostics' / 'frames' / 'cavebot_trace.jsonl',
        root / 'diagnostics' / 'frames_emergency' / 'cavebot_trace.jsonl',
        root / 'diagnostics' / 'frames_full' / 'cavebot_trace.jsonl',
    ]
    existing = [p for p in fixed if p.exists()]
    existing.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return existing


def _extract_trace_phrases(path: Path, *, map_enhanced_to_blessing: bool) -> tuple[list[str], int]:
    phrases: list[str] = []
    actions = 0
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        text = str(line or '').strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get('event', '')).strip() != 'WAYPOINT_ACTION':
            continue
        actions += 1
        phrase = _map_phrase(str(row.get('phrase', '') or ''), map_enhanced_to_blessing=map_enhanced_to_blessing)
        if phrase:
            phrases.append(phrase)
    return phrases, actions


def _runtime_summary(runtime_log: Path) -> dict:
    if not runtime_log.exists():
        return {
            'exists': False,
            'success_events': 0,
            'abort_events': 0,
        }

    rows: list[dict] = []
    for line in runtime_log.read_text(encoding='utf-8', errors='replace').splitlines():
        text = str(line or '').strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue

        gate = str(row.get('gate', '') or '').strip().lower()
        if not gate.startswith('cavebot'):
            continue
        rows.append(row)

    sessions: list[list[dict]] = []
    current: list[dict] = []
    for row in rows:
        event = str(row.get('event', '') or '').strip().lower()
        tick_index = row.get('tick_index')
        if event == 'tick' and isinstance(tick_index, int) and int(tick_index) == 0 and current:
            sessions.append(current)
            current = []
        current.append(row)
    if current:
        sessions.append(current)

    latest = sessions[-1] if sessions else []
    success_events = 0
    abort_events = 0
    for row in latest:
        ev = str(row.get('event', '')).strip().lower()
        if ev == 'success':
            success_events += 1
        reason = str(row.get('abort_reason', 'none') or 'none').strip().lower()
        if reason not in {'', 'none'}:
            abort_events += 1

    return {
        'exists': True,
        'success_events': int(success_events),
        'abort_events': int(abort_events),
        'session_events': int(len(latest)),
        'session_count': int(len(sessions)),
    }


def _latest_fatal(root: Path) -> dict:
    p = root / 'diagnostics' / 'fatal.log'
    data = _read_json(p)
    reason = str(data.get('reason') or '').strip()
    return {
        'path': str(p),
        'exists': p.exists(),
        'reason': reason,
    }


def _contains_all(haystack: Iterable[str], needles: Iterable[str]) -> bool:
    hs = list(haystack)
    return all(str(n) in hs for n in needles)


def main() -> int:
    ap = argparse.ArgumentParser(description='Evaluate REAL UI cavebot run artifacts and NPC dialog evidence.')
    ap.add_argument('--repo-root', default='.', help='Repository root path')
    ap.add_argument('--frames-dir', default='', help='Optional explicit frames directory containing cavebot_trace.jsonl')
    ap.add_argument('--expect-phrases', default='hi,blessing,yes', help='Comma-separated expected NPC phrases')
    ap.add_argument('--map-enhanced-to-blessing', action='store_true', help='Treat phrase enhanced as blessing for operational mapping')
    ap.add_argument('--out', default='diagnostics/ui_real_cavebot_eval.json', help='Output JSON report path')
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    expected = _parse_expected(args.expect_phrases)

    if args.frames_dir:
        trace_path = Path(args.frames_dir).resolve() / 'cavebot_trace.jsonl'
    else:
        traces = _candidate_trace_paths(root)
        trace_path = traces[0] if traces else (root / 'diagnostics' / 'frames' / 'cavebot_trace.jsonl')

    runtime_log = root / 'diagnostics' / 'runtime.log'
    runtime = _runtime_summary(runtime_log)
    fatal = _latest_fatal(root)

    trace_exists = trace_path.exists()
    trace_phrases: list[str] = []
    action_events = 0
    if trace_exists:
        trace_phrases, action_events = _extract_trace_phrases(
            trace_path,
            map_enhanced_to_blessing=bool(args.map_enhanced_to_blessing),
        )

    phrase_ok = _contains_all(trace_phrases, expected) if expected else True
    run_ok = bool(runtime.get('success_events', 0)) and int(runtime.get('abort_events', 0)) == 0
    certified = bool(trace_exists and phrase_ok and run_ok)

    reasons: list[str] = []
    if not trace_exists:
        reasons.append('missing_cavebot_trace')
    if not run_ok:
        reasons.append('runtime_not_successful')
    if trace_exists and not phrase_ok:
        reasons.append('expected_phrases_missing')

    if fatal.get('exists') and str(fatal.get('reason') or '').strip():
        reasons.append(f"latest_fatal_reason:{fatal.get('reason')}")

    report = {
        'certified': bool(certified),
        'expected_phrases': expected,
        'trace_phrases': trace_phrases,
        'trace_action_events': int(action_events),
        'trace_path': str(trace_path),
        'runtime_log': {
            'path': str(runtime_log),
            **runtime,
        },
        'fatal': fatal,
        'map_enhanced_to_blessing': bool(args.map_enhanced_to_blessing),
        'reasons': reasons,
    }

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = (root / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if bool(certified) else 1


if __name__ == '__main__':
    raise SystemExit(main())
