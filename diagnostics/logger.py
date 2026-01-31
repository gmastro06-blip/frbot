from __future__ import annotations

import logging
from pathlib import Path


def configure_logger() -> logging.Logger:
    """Persistent logger. No console dependency."""
    Path('diagnostics').mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger('frbot')
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = logging.FileHandler(Path('diagnostics') / 'runtime.log', encoding='utf-8')
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    logger.addHandler(handler)

    return logger
