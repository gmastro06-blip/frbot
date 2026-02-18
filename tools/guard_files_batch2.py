import re
from pathlib import Path

FILES = [
    'runtime/startup_guards.py',
    'runtime/runner.py',
    'runtime/targeting_runner.py',
    'runtime/trade_runner.py',
    'runtime/preflight.py',
]

pattern = re.compile(r"(?m)^(?P<indent>[ \t]*)except\s+Exception\s*:\n(?P<indent2>[ \t]*)pass\s*$")

for f in FILES:
    p = Path(f)
    if not p.exists():
        print(f'missing: {f}')
        continue
    src = p.read_text(encoding='utf-8')
    def repl(m):
        ind = m.group('indent')
        ind2 = m.group('indent2')
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
