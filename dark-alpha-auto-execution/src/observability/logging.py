"""Structured JSON-line logging using structlog.

Writes to logs/YYYY-MM-DD/app.log (one file per day) and stdout.
Call configure_logging() once at application startup.
"""

import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog


def configure_logging(log_dir: str | None = None, level: str = "INFO") -> None:
    log_root = Path(log_dir or os.getenv("LOG_DIR") or "logs")
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    log_file = log_root / today / "app.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_file)
    stream_handler = logging.StreamHandler(sys.stdout)

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[file_handler, stream_handler],
    )

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
