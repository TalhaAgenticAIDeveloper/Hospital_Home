"""
Structured application logging.

Provides a configured logger that filters sensitive fields.
NEVER log: passwords, password hashes, JWT tokens, refresh tokens,
database credentials, or JWT secrets.
"""

import logging
import sys
from typing import Set

from app.core.config import get_settings

settings = get_settings()

# Fields that must NEVER appear in logs
SENSITIVE_FIELDS: Set[str] = {
    "password",
    "password_hash",
    "hashed_password",
    "access_token",
    "refresh_token",
    "token",
    "jwt_secret",
    "secret_key",
    "database_url",
    "authorization",
}


class SensitiveFilter(logging.Filter):
    """Filter that redacts sensitive fields from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg") and isinstance(record.msg, str):
            msg_lower = record.msg.lower()
            for field in SENSITIVE_FIELDS:
                if field in msg_lower:
                    record.msg = f"[REDACTED — contained sensitive field: {field}]"
                    break
        return True


def get_logger(name: str) -> logging.Logger:
    """
    Get a configured logger instance.

    Args:
        name: Logger name (typically __name__).

    Returns:
        Configured logging.Logger instance.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handler.addFilter(SensitiveFilter())
        logger.addHandler(handler)

    logger.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)
    logger.propagate = False

    return logger
