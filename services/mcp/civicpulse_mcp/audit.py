"""Structured audit events for MCP tool calls.

Events go to stderr as JSON lines (stdout is reserved for the stdio transport) and, optionally, to a
JSONL file named by ``CIVICPULSE_MCP_AUDIT_LOG``. Arguments are sanitized: secret-looking keys are
redacted, strings truncated, and nested values bounded.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sys
import threading
from typing import Any

_SECRET_KEY = re.compile(r"(key|token|secret|password|authorization|credential)", re.I)
_LOCK = threading.Lock()
logger = logging.getLogger("civicpulse_mcp.audit")


def sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "<nested>"
    if isinstance(value, dict):
        return {
            str(k)[:60]: ("<redacted>" if _SECRET_KEY.search(str(k)) else sanitize(v, depth + 1))
            for k, v in list(value.items())[:20]
        }
    if isinstance(value, list | tuple):
        return [sanitize(v, depth + 1) for v in list(value)[:20]]
    if isinstance(value, str):
        return value[:120]
    if value is None or isinstance(value, bool | int | float):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return sanitize(value.model_dump(mode="json"), depth + 1)
    return str(value)[:120]


def audit_event(event: str, **fields: Any) -> dict[str, Any]:
    record = {"ts": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"), "event": event}
    record.update({k: sanitize(v) for k, v in fields.items()})
    line = json.dumps(record, default=str)
    with _LOCK:
        print(line, file=sys.stderr, flush=True)
        path = os.environ.get("CIVICPULSE_MCP_AUDIT_LOG")
        if path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    return record
