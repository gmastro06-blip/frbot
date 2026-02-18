# TODO: runtime/config_schema.py

Issues detected by `ruff`:
- `E721` comparisons to types using `==` (use `is` or `isinstance`).

Suggested fixes (mechanical):
- Replace `if schema.type_hint == bool:` with `if schema.type_hint is bool:` and similarly for `int` and `float`.
- Run `ruff`/`mypy --strict` for validation.
