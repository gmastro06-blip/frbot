import re
from pathlib import Path

FILES = [
    'runtime/capture_source.py',
    'runtime/chat_loot_semantics.py',
    'runtime/looting_basic_runner.py',
    'runtime/inventory_semantics.py',
    'runtime/route_recorder.py',
]

pattern = re.compile(r"(^[ \t]*)except\s+Exception\s*:\n(^[ \t]*)pass\s*$", re.MULTILINE)

for f in FILES:
    p = Path(f)
    if not p.exists():
        print(f'missing: {f}')
        continue
    src = p.read_text(encoding='utf-8')
    def repl(m):
        ind = m.group(1)
        ind2 = m.group(2)
        return (
            f"{ind}except Exception:\n"
            f"{ind2}try:\n"
            f"{ind2}    from runtime.error_policy import should_reraise\n\n"
            f"{ind2}    if should_reraise():\n"
            f"{ind2}        raise\n"
            f"{ind2}except Exception:\n"
            f"{ind2}    pass"
        )
    new, n = pattern.subn(repl, src)
    if n > 0:
        p.write_text(new, encoding='utf-8')
        print(f'patched {f}: {n} changes')
    else:
        print(f'no changes in {f}')
