"""Deterministic safety policy: input screening, tool allowlist, argument validation, output redaction.

These controls do not depend on the language model behaving well: even a fully compromised model can
only call allow-listed, schema-validated, read-only MCP tools, within a call budget.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import jsonschema

ALLOWED_TOOLS = frozenset(
    {
        "get_service_request_trends",
        "compare_boroughs",
        "get_category_breakdown",
        "forecast_request_volume",
        "predict_closure_delay",
        "detect_unusual_spikes",
        "explain_model_prediction",
        "get_dataset_quality",
        "get_model_evaluation",
        "generate_investigation_report",
    }
)

_SECRET = re.compile(
    r"\b(api[ _-]?keys?|secret|password|passwd|credential|access token|bearer token|auth token|env(ironment)? "
    r"var(iable)?s?|system prompt|hidden prompt|your instructions|developer message|private key)|"
    r"(?<![\w.])\.env\b",
    re.I,
)
_SQL = re.compile(
    r"\bselect\s+(\*|[a-z_]+(\s*,\s*[a-z_]+)*)\s+from\b|\binsert\s+into\b|\bdelete\s+from\b|\bdrop\s+(table|database|"
    r"view)\b|\balter\s+table\b|\bcreate\s+table\b|\btruncate\s+table\b|\bupdate\s+\w+\s+set\b|\battach\s+database\b|"
    r"\b(run|execute|write|give me|show me|let me run)\b[^.?!]{0,40}\b(sql|queries|query)\b|\bsql\b|\braw "
    r"(database|table|tables|data ?base files?)\b|\bunrestricted (query|access)\b|\b(duckdb|parquet) files?\b|"
    r"\bshell command\b|\b(rm|del|curl|wget|powershell|bash)\s+-|\bread (the )?file\b|\bfile ?system\b",
    re.I,
)
_INJECTION = re.compile(
    r"(ignore|disregard|forget|override)\b[^.?!]{0,40}\b(instructions|rules|guidelines|policy|policies|prompt)|"
    r"\byou are now\b|\bdeveloper mode\b|\bjailbreak\b|\bact as (an? )?(unrestricted|unfiltered)|\bDAN\b|"
    r"<\s*/?\s*(system|assistant|tool)\s*>|\bnew instructions\s*:",
    re.I,
)
_SECRET_OUTPUT = re.compile(r"(sk-[A-Za-z0-9_\-]{16,}|Bearer\s+[A-Za-z0-9_\-.=]{16,}|CIVICPULSE_[A-Z_]*KEY\s*=\s*\S+)")


@dataclass(frozen=True)
class Screening:
    allowed: bool
    category: str  # ok | empty | too_long | secret_request | sql_request | injection | invalid_characters
    message: str


def normalize_question(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch)[0] != "C")
    return re.sub(r"\s+", " ", text).strip()


def screen_question(question: str, max_chars: int) -> Screening:
    if not question:
        return Screening(False, "empty", "Please enter a question about NYC 311 service requests.")
    if len(question) > max_chars:
        return Screening(False, "too_long", f"Questions are limited to {max_chars} characters.")
    if _SECRET.search(question):
        return Screening(
            False,
            "secret_request",
            "I can't share credentials, configuration, system instructions or other secrets. I can answer analytical "
            "questions about the NYC 311 snapshot.",
        )
    if _SQL.search(question):
        return Screening(
            False,
            "sql_request",
            "I can't run SQL, shell commands or access raw database files. The analyst only uses a fixed set of "
            "read-only, typed analytical tools (trends, comparisons, breakdowns, forecasts, anomaly flags, data "
            "quality and model evaluation).",
        )
    if _INJECTION.search(question):
        return Screening(
            False,
            "injection",
            "Requests to change or ignore the analyst's rules are not followed. Please ask an analytical question "
            "about the NYC 311 data.",
        )
    return Screening(True, "ok", "")


def validate_tool_call(name: str, arguments: Any, schemas: dict[str, dict[str, Any]]) -> str | None:
    """Return an error message if the call is not allowed or the arguments violate the tool's input schema."""
    if name not in ALLOWED_TOOLS:
        return f"tool '{name}' is not permitted"
    if name not in schemas:
        return f"tool '{name}' was not offered by the MCP server"
    if not isinstance(arguments, dict):
        return "tool arguments must be a JSON object"
    schema = dict(schemas[name])
    schema.setdefault("additionalProperties", False)
    try:
        jsonschema.validate(arguments, schema, format_checker=jsonschema.FormatChecker())
    except jsonschema.ValidationError as exc:
        path = ".".join(str(p) for p in exc.absolute_path) or "(root)"
        return f"invalid arguments at {path}: {exc.message[:200]}"
    return None


def redact_output(text: str) -> str:
    return _SECRET_OUTPUT.sub("[redacted]", text)


DELAY_ACTION = re.compile(
    r"\b(prioriti[sz]e|priority|rank|ranking|allocate|allocation|which (requests|buildings|communities|"
    r"neighbou?rhoods)|where should|dispatch|send (inspectors|crews)|target)\b",
    re.I,
)


def asks_for_delay_action(question: str) -> bool:
    q = question.lower()
    return ("delay" in q or "closure" in q or "close" in q or "risk" in q or "backlog" in q) and bool(
        DELAY_ACTION.search(q)
    )
