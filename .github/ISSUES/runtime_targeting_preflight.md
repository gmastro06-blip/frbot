# TODO: runtime/targeting_preflight.py

Issues detected by `ruff`:
- `F541` f-strings used without placeholders in debug `print()` calls.

Suggested fixes (mechanical):
- Replace `print(f'...')` with `print('...')` for messages without placeholders.
- Re-run `ruff` and confirm no behavior changes.
