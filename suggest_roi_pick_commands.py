from __future__ import annotations

import runpy
from pathlib import Path


def main() -> int:
    script = Path(__file__).resolve().parent / "tools" / "suggest_roi_pick_commands.py"
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
