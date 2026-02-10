from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger(*, log_path: str = "runtime_ui.log") -> logging.Logger:
    """Configure a file-backed logger for the UI.

    - Writes to `runtime_ui.log` by default
    - Uses rotation to avoid unbounded growth
    """
    logger = logging.getLogger("frbot_ui")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    path = Path(log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    handler = RotatingFileHandler(
        filename=str(path),
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(fmt)

    logger.addHandler(handler)
    logger.propagate = False
    return logger
