"""Structured (JSON-lines) logging using only the standard library."""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

_SECRET_PATTERNS = [
    re.compile(r"(?i)(x-app-token|app_token|api[_-]?key|authorization)(['\"]?\s*[:=]\s*['\"]?)([^\s'\",}]+)"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
]


def redact(text: str) -> str:
    out = _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    return _SECRET_PATTERNS[1].sub("sk-***", out)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload.update({k: redact(str(v)) if isinstance(v, str) else v for k, v in extra.items()})
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    # httpx logs every request URL at INFO; our client logs its own structured events.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def log(logger: logging.Logger, level: int, msg: str, **ctx: Any) -> None:
    logger.log(level, msg, extra={"ctx": ctx})
