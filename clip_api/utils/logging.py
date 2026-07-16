import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from ..config import LOG_BACKUP_COUNT, LOG_FILE_PATH, LOG_LEVEL, LOG_MAX_BYTES


class JsonFormatter(logging.Formatter):
    """Emit structured JSON log records for production observability."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = record.event
        if hasattr(record, "context"):
            payload["context"] = record.context
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


def setup_logging(level: str = LOG_LEVEL) -> None:
    """Configure root logging with stdout and rotated file handlers."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    if root_logger.handlers:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()

    formatter = JsonFormatter()
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    handlers = [stream_handler]
    file_path = os.environ.get("LOG_FILE_PATH", LOG_FILE_PATH)
    if file_path:
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        handlers=handlers,
        force=True,
    )
