# TODO: runtime/entrypoint_registry.py

Issues detected by `ruff`:
- `F401` unused imports (`os` imported but not used).
- `F541` f-strings without placeholders passed to `write_fatal`.

Suggested fixes (mechanical):
- Remove `import os`.
- Replace `write_fatal(f'entrypoint_not_found', ...)` with `write_fatal('entrypoint_not_found', ...)`.
- Run `ruff` and ensure no behavioral change.
