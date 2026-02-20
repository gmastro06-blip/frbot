from __future__ import annotations
import json
from pathlib import Path
import sys

p = Path('diagnostics') / 'waypoints_validation.json'
if not p.exists():
    print('MISSING', p)
    raise SystemExit(2)
data = json.loads(p.read_text(encoding='utf-8'))
valid = [f['file'] for f in data.get('files', []) if f.get('valid')]
for v in valid:
    print(v)
