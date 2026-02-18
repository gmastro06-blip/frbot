# TODO: runtime/battle_list_ocr.py

Issues detected by `ruff`:
- `F541` f-strings used without placeholders (remove `f` prefix where no `{}` present).
- `F401` unused imports in some helper blocks.

Suggested fixes (mechanical):
- Replace `print(f'...')` with `print('...')` where the string has no placeholders.
- Remove unused imports (e.g., `ImageFilter`, `unicodedata`) or move them inside functions where used.
- Prefer to keep behavior unchanged; make minimal edits and run `ruff`/`mypy` afterwards.
