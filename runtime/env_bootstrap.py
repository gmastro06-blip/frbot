from __future__ import annotations

from pathlib import Path


def _parse_env_line(raw: str) -> tuple[str, str] | None:
    text = str(raw or "").strip()
    if not text or text.startswith("#"):
        return None

    if text.lower().startswith("export "):
        text = text[7:].strip()

    if "=" not in text:
        return None

    key_raw, value_raw = text.split("=", 1)
    key = str(key_raw or "").strip()
    if not key:
        return None

    value = str(value_raw or "").strip()
    if len(value) >= 2 and ((value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'"))):
        value = value[1:-1]
    return (key, value)


def load_repo_env(*, override: bool = False, prefix: str = "FRBOT_") -> dict[str, str]:
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / ".env"
    if not env_path.exists() or not env_path.is_file():
        return {}

    loaded: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if prefix and not str(key).startswith(str(prefix)):
            continue
        loaded[str(key)] = str(value)

    if not loaded:
        return {}

    import os

    applied: dict[str, str] = {}
    for key, value in loaded.items():
        if not override and key in os.environ:
            continue
        os.environ[str(key)] = str(value)
        applied[str(key)] = str(value)
    return applied
