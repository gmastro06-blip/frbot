from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_tool_env(caller_file: str) -> None:
    """Ensure repo-root imports and load repository environment variables.

    Works for scripts executed directly via `python tools/<script>.py`.
    """

    repo_root = Path(caller_file).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from runtime.env_bootstrap import load_repo_env

    load_repo_env()
