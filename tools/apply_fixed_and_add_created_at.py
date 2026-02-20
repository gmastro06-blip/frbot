#!/usr/bin/env python3
"""Apply all *.fixed.json: ensure each waypoint has created_at, write back fixed file,
create <orig>.bak if needed and replace original with fixed.
Writes a summary to diagnostics/waypoints_apply_fixed_created_at.json
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WAYPOINTS = ROOT / "Waypoints"
DIAG = ROOT / "diagnostics"
OUT = DIAG / "waypoints_apply_fixed_created_at.json"

fixed_files = list(WAYPOINTS.rglob("*.fixed.json"))
summary = {"total_fixed_files": len(fixed_files), "updated_fixed": 0, "applied_to_original": 0, "skipped": 0, "files": {}}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

for fx in fixed_files:
    rec = {"fixed": str(fx), "updated_fixed": False, "orig": None, "applied": False, "reason": None}
    try:
        data = json.loads(fx.read_text(encoding='utf-8'))
        wps = data.get('waypoints', [])
        changed = False
        for wp in wps:
            if 'created_at' not in wp or wp.get('created_at') in (None, ''):
                wp['created_at'] = now_iso()
                changed = True
        if changed:
            fx.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            rec['updated_fixed'] = True
            summary['updated_fixed'] += 1

        # derive original path: strip the trailing '.fixed.json' suffix
        if not fx.name.endswith('.fixed.json'):
            rec['reason'] = 'unexpected_name'
            summary['skipped'] += 1
            summary['files'][str(fx)] = rec
            continue
        orig_name = fx.name[:-len('.fixed.json')]
        orig_path = fx.with_name(orig_name)
        rec['orig'] = str(orig_path)
        if not orig_path.exists():
            rec['reason'] = 'original_missing'
            summary['skipped'] += 1
            summary['files'][str(fx)] = rec
            continue
        # backup original if not exists
        bak = orig_path.with_name(orig_path.name + '.bak')
        if not bak.exists():
            shutil.copy2(orig_path, bak)
        # copy fixed over original
        shutil.copy2(fx, orig_path)
        rec['applied'] = True
        summary['applied_to_original'] += 1
    except Exception as e:
        rec['reason'] = f'error:{e}'
        summary['skipped'] += 1
    summary['files'][str(fx)] = rec

OUT.write_text(json.dumps(summary, indent=2), encoding='utf-8')
print('Done. fixed files:', summary['total_fixed_files'], 'updated_fixed:', summary['updated_fixed'], 'applied:', summary['applied_to_original'])
