"""Structured logging setup: console + JSON-lines file."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import OrchestratorConfig


class JSONFormatter(logging.Formatter):
    """JSON Lines format for structured log files."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
        })


def setup_logger(config: OrchestratorConfig) -> logging.Logger:
    """Create a logger with console and optional JSON-lines file handlers."""
    logger = logging.getLogger("orchestrator")
    logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

    # Don't add handlers if already configured (avoids duplicates on re-init)
    if logger.handlers:
        return logger

    # Console handler (human-readable)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(console)

    # File handler (JSON lines)
    if config.structured_log:
        log_dir = config.project_dir / config.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(JSONFormatter())
        logger.addHandler(file_handler)

    return logger
