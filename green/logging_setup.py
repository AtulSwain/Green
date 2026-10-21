"""Logging configuration: rotating file log with secret redaction."""

from __future__ import annotations

import logging
import logging.handlers
from collections import deque
from pathlib import Path

from .security.secret_redaction import RedactingFilter

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class RecentErrorsHandler(logging.Handler):
    """Keeps the last warnings/errors (already redacted) for the diagnostics screen."""

    def __init__(self, capacity: int = 50):
        super().__init__(level=logging.WARNING)
        self.records: deque[str] = deque(maxlen=capacity)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Exception tracebacks are left out: they can contain local file paths and data.
            line = self.format(logging.makeLogRecord({**record.__dict__, "exc_info": None, "exc_text": None}))
            self.records.append(line)
        except Exception:
            self.handleError(record)


def configure_logging(log_dir: Path | None, level: int = logging.INFO) -> RecentErrorsHandler:
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, "_green", False):
            root.removeHandler(handler)
    redactor = RedactingFilter()
    handlers: list[logging.Handler] = []
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "green.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(FORMAT))
        handlers.append(file_handler)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(FORMAT))
    console.setLevel(logging.WARNING)
    handlers.append(console)
    recent = RecentErrorsHandler()
    handlers.append(recent)
    for handler in handlers:
        handler.addFilter(redactor)
        handler._green = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    return recent
