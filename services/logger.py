"""
services/logger.py
──────────────────
Configures a rotating file + streaming console logger used across
all services.  Call get_logger() once per module.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config.settings import LOGS_DIR, get_settings

_INITIALIZED = False


def _initialize_logging() -> None:
    """Configure root logger once.  Subsequent calls are no-ops."""
    global _INITIALIZED
    if _INITIALIZED:
        return

    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)-35s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Console handler ──────────────────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    # ── Rotating file handler (max 5 MB × 5 files) ──────────────────────
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / "job_hunter.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Silence noisy third-party loggers
    for noisy in ("httpcore", "httpx", "openai._base_client", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger, initialising the root logger on first call.

    Usage:
        from services.logger import get_logger
        log = get_logger(__name__)
        log.info("Hello")
    """
    _initialize_logging()
    return logging.getLogger(name)
