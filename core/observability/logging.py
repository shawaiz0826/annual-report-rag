"""Structured (JSON lines) logging setup."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """One JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as a JSON line."""
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)[-2000:]
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", json_lines: bool = True) -> None:
    """Configure the root logger once; quiet noisy third-party loggers."""
    root = logging.getLogger()
    if getattr(root, "_arr_configured", False):
        root.setLevel(level.upper())
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter() if json_lines else logging.Formatter("%(levelname)s %(name)s: %(message)s")
    )
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in (
        "httpx",
        "httpcore",
        "openai",
        "chromadb",
        "sentence_transformers",
        "pdfminer",
        "urllib3",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._arr_configured = True  # type: ignore[attr-defined]
