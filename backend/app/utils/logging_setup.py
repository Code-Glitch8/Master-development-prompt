from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from ..config import get_settings


def setup_logging() -> None:
    s = get_settings()
    s.logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("projectpilot")
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(
        s.logs_dir / "project-pilot.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"projectpilot.{name}")
