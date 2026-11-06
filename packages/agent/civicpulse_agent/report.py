"""Investigation reports: a versioned, checksummed JSON document plus Markdown and print-friendly HTML renderings.

A report is built only from what the deterministic pipeline verified: the validated claims, the MCP evidence they
cite and the rendered answer that passed validation. It is reproducible without the language model:
``verify_report`` recomputes every evidence checksum, re-extracts facts from the stored evidence and re-validates
each claim exactly as the provider proposed it, then checks that the re-computed values equal the reported ones.

Reports never contain provider keys, request headers, the access token, internal paths or server configuration:
arguments and evidence pass through ``_sanitize`` and only an explicit allow-list of fields is copied.

Usage (offline verification): ``python -m civicpulse_agent.report verify report.json``
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

from civicpulse_agent.claims import Claim, ClaimValidator, build_fact_index, fmt_value
from civicpulse_agent.evidence import Evidence
from civicpulse_agent.orchestrator import InvestigationResult

SCHEMA_VERSION = "civicpulse.investigation-report/1"
REPORT_FORMATS = ("json", "markdown", "html")

_SECRETISH = re.compile(r"(secret|token|password|passwd|api[_-]?key|authorization|cookie|header|credential)", re.I)
_SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+|[A-Za-z]:\\\S+|/(?:home|Users|root|etc|var)/\S+)")
_EVIDENCE_FIELDS = (
    "evidence_id",
    "tool",
    "arguments",
    "status",
    "capability",
    "dataset_id",
    "data_build_id",
    "artifact",
    "data_cutoff",
    "last_complete_date",
    "metric_definitions",
    "summary",
    "key_measurements",
    "model_name",
    "model_version",
    "model_run_id",
    "query",
    "limitations",
    "uncertainty",
    "data",
    "coverage",
)
STANDARD_LIMITATIONS = [
    "311 service requests are reports made by the public, not verified incidents.",
    "Raw counts are not rates: boroughs differ in population, housing stock and reporting behaviour.",
    "Differences between areas or periods do not by themselves show unequal service or cause and effect.",
    "The data is a historical snapshot, not a live feed.",
]


# ---------------------------------------------------------------------------------------------------- helpers
def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sanitize(value: Any, depth: int = 0) -> Any:
    """Drop secret-looking keys and redact secret-looking strings (keys, bearer tokens, absolute paths)."""
    if depth > 12:
        return "<nested>"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, depth + 1) for k, v in value.items() if not _SECRETISH.search(str(k))}
    if isinstance(value, list):
        return [_sanitize(v, depth + 1) for v in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[redacted]", value)
    return value


def evidence_checksum(ev: dict[str, Any]) -> str:
    """Checksum over the evidence content stored in the report (the ``checksum`` field itself excluded)."""
    return sha256_of({k: v for k, v in ev.items() if k != "checksum"})


def content_checksum(report: dict[str, Any]) -> str:
    """Checksum over the report body. Excludes the generation timestamp so rebuilding yields the same value."""
    return sha256_of({k: v for k, v in report.items() if k not in ("generated_at", "content_checksum")})


def _answer_verified(res: InvestigationResult) -> bool:
    v = res.validation
    if res.status in ("answered", "clarification"):
        return v.get("ok") is True
    if res.status == "fallback":
        return bool((v.get("fallback") or {}).get("ok"))
    return False


def _title(question: str) -> str:
    q = " ".join(question.split())
    return "Investigation: " + (q if len(q) <= 90 else q[:87].rstrip() + "...")


# ---------------------------------------------------------------------------------------------------- build
def build_report(res: InvestigationResult, generated_at: dt.datetime | None = None) -> dict[str, Any]:
    """Report document for one investigation. Only validated claims and the verified answer are included."""
    verified = _answer_verified(res)
    claims = [_sanitize(c) for c in res.claims if c.get("status") == "valid"] if verified else []
    evidence: list[dict[str, Any]] = []
    for e in res.evidence:
        raw = {k: e.get(k) for k in _EVIDENCE_FIELDS}
        if isinstance(raw.get("artifact"), str):  # file name only: never a server path
            raw["artifact"] = str(raw["artifact"]).replace("\\", "/").rsplit("/", 1)[-1]
        item = _sanitize(raw)
        item["checksum"] = evidence_checksum(item)
        evidence.append(item)
    tools_used = [
        {
            "step": t.step,
            "tool": t.name,
            "status": t.status,
            "evidence_id": t.evidence_id,
            "duration_ms": t.duration_ms,
            "arguments": _sanitize(t.arguments or {}),
        }
        for t in res.trace
        if t.kind == "tool"
    ]
    models = [
        {
            "evidence_id": e["evidence_id"],
            "tool": e["tool"],
            "capability": e["capability"],
            "model_name": e.get("model_name"),
            "model_version": e.get("model_version"),
            "model_run_id": e.get("model_run_id"),
        }
        for e in evidence
        if e.get("model_version") or e.get("model_run_id")
    ]
    limitations: list[str] = []
    for item in [*STANDARD_LIMITATIONS, *(x for e in evidence for x in (e.get("limitations") or []))]:
        if item not in limitations:
            limitations.append(item)
    fr = res.freshness
    snapshot_ids = sorted({str(e["data_build_id"]) for e in evidence if e.get("data_build_id")})
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_id": res.investigation_id,
        "title": _title(res.question),
        "question": res.question,
        "status": res.status,
        "answer_verified": verified,
        "verified_answer": res.answer if verified else None,
        "status_message": None if verified else res.answer,
        "filters": [
            {"evidence_id": e["evidence_id"], "tool": e["tool"], "arguments": e.get("arguments") or {}}
            for e in evidence
        ],
        "periods": res.periods,
        "dataset": {
            "dataset_id": next((e["dataset_id"] for e in evidence if e.get("dataset_id")), None),
            "snapshot_id": fr.get("build_id"),
            "evidence_snapshot_ids": snapshot_ids,
            "data_cutoff": fr.get("data_cutoff"),
            "first_date": fr.get("first_date"),
            "last_complete_date": fr.get("last_complete_date"),
            "live": False,
        },
        "model_versions_current": fr.get("model_versions") or {},
        "claims": claims,
        "evidence": evidence,
        "tables": _sanitize(res.charts),
        "models": models,
        "warnings": [w.model_dump() for w in res.warnings],
        "limitations": limitations,
        "mcp": {
            "tools_used": tools_used,
            "protocol_version": res.mcp.get("protocol_version"),
            "server": res.mcp.get("server"),
            "server_version": res.mcp.get("server_version"),
            "transport": res.mcp.get("transport"),
        },
        "provider": {
            "name": res.provider.get("name"),
            "model": res.provider.get("model"),
            "live_llm": bool(res.provider.get("live_llm")),
        },
        "validation": {
            "ok": verified,
            "claims_reported": len(claims),
            "regenerations": res.validation.get("regenerations", 0),
            "method": "Deterministic: structured claims re-checked against MCP evidence facts; prose contains no "
            "numbers of its own. Re-run offline with `python -m civicpulse_agent.report verify <file>`.",
        },
        "generated_at": (generated_at or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds"),
    }
    report["content_checksum"] = content_checksum(report)
    return report


# ---------------------------------------------------------------------------------------------------- verify
def _evidence_from_report(item: dict[str, Any]) -> Evidence:
    result = {
        "tool": item.get("tool", ""),
        "status": item.get("status", ""),
        "capability": item.get("capability", ""),
        "data": item.get("data") or {},
        "coverage": item.get("coverage"),
        "query": item.get("query") or {},
        "summary": item.get("summary", ""),
        "key_measurements": item.get("key_measurements") or [],
        "provenance": {
            "dataset_id": item.get("dataset_id"),
            "data_build_id": item.get("data_build_id"),
            "artifact": item.get("artifact"),
            "data_cutoff": item.get("data_cutoff"),
            "last_complete_date": item.get("last_complete_date"),
            "model_name": item.get("model_name"),
            "model_version": item.get("model_version"),
            "model_run_id": item.get("model_run_id"),
        },
        "metric_definitions": item.get("metric_definitions") or {},
        "limitations": item.get("limitations") or [],
        "uncertainty": item.get("uncertainty") or [],
    }
    return Evidence.from_tool_result(str(item["evidence_id"]), result, item.get("arguments") or {}, 0.0)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(b)))
    return bool(a == b)


def verify_report(report: dict[str, Any]) -> dict[str, Any]:
    """Re-validate a report offline: schema, checksums, and every claim against facts re-extracted from evidence."""
    problems: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"unsupported schema_version {report.get('schema_version')!r}")
        return {"ok": False, "problems": problems, "claims_checked": 0}
    if report.get("content_checksum") != content_checksum(report):
        problems.append("content_checksum does not match the report body")
    evidence_items = report.get("evidence") or []
    for item in evidence_items:
        if item.get("checksum") != evidence_checksum(item):
            problems.append(f"evidence {item.get('evidence_id')}: checksum mismatch")
    try:
        evs = [_evidence_from_report(i) for i in evidence_items]
    except Exception as exc:  # malformed evidence in a hand-edited file
        problems.append(f"evidence could not be parsed: {type(exc).__name__}")
        return {"ok": False, "problems": problems, "claims_checked": 0}
    facts, conflicts = build_fact_index(evs)
    validator = ClaimValidator({e.evidence_id: e for e in evs}, facts, conflicts, report.get("model_versions_current"))
    stored = report.get("claims") or []
    proposed: list[Claim] = []
    for c in stored:
        try:
            proposed.append(Claim.model_validate(c.get("input") or {}))
        except Exception:
            problems.append(f"claim {c.get('id')}: original claim input missing or invalid")
    checks = {chk.claim.id: chk for chk in validator.validate(proposed)}
    for c in stored:
        chk = checks.get(str(c.get("id")))
        if chk is None:
            continue
        if not chk.ok:
            problems.append(f"claim {c.get('id')}: no longer valid ({chk.status}: {chk.reason})")
        elif not _same(chk.computed, c.get("value")):
            problems.append(f"claim {c.get('id')}: value {c.get('value')!r} != recomputed {chk.computed!r}")
    if report.get("answer_verified") and report.get("verified_answer"):
        for c in stored:
            if c.get("status") == "valid" and isinstance(c.get("value"), (int, float)):
                shown = fmt_value(c["value"], str(c.get("unit") or ""))
                if shown not in report["verified_answer"] and str(c.get("type")) != "comparison":
                    problems.append(f"claim {c.get('id')}: value {shown} does not appear in the verified answer")
    return {"ok": not problems, "problems": problems, "claims_checked": len(stored)}


# ---------------------------------------------------------------------------------------------------- render
def _claim_rows(report: dict[str, Any]) -> list[list[str]]:
    rows = []
    for c in report.get("claims") or []:
        period = " to ".join(x for x in (c.get("period_start"), c.get("period_end")) if x) or "-"
        value = c.get("value")
        shown = fmt_value(value, str(c.get("unit") or "")) if isinstance(value, (int, float, str)) else "-"
        rows.append(
            [
                str(c.get("id")),
                str(c.get("metric_id") or "-"),
                str(c.get("geography_id") or "-"),
                str(c.get("category_id") or "-"),
                period,
                shown,
                str(c.get("unit") or "-"),
                ", ".join(c.get("evidence_ids") or []) or "-",
            ]
        )
    return rows


CLAIM_HEADERS = ["Claim", "Metric", "Geography", "Category", "Period", "Value", "Unit", "Evidence"]


def _md_cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(_md_cell(c) for c in r) + " |" for r in rows]
    return out


def _table_rows(t: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
    """A chart from the investigation as a plain table: one row per x value, one column per series."""
    series = [x for x in (t.get("series") or []) if isinstance(x, dict)]
    if not series:
        return [], []
    xs: list[str] = []
    values: dict[tuple[str, str], Any] = {}
    for ser in series:
        for pt in ser.get("points") or []:
            x = str(pt.get("x"))
            if x not in xs:
                xs.append(x)
            values[(str(ser.get("name")), x)] = pt.get("y")
    names = [str(ser.get("name")) for ser in series]
    rows = [[x] + ["" if values.get((n, x)) is None else str(values[(n, x)]) for n in names] for x in xs[:60]]
    return ["x", *names], rows


def render_markdown(report: dict[str, Any]) -> str:
    ds = report["dataset"]
    lines = [
        f"# {report['title']}",
        "",
        f"- **Question:** {report['question']}",
        f"- **Status:** {report['status']} ({_verified(report)})",
        f"- **Generated:** {report['generated_at']}",
        f"- **Report ID:** {report['report_id']} · schema `{report['schema_version']}`",
        f"- **Content checksum (SHA-256):** `{report['content_checksum']}`",
        "",
        "## Data",
        "",
        f"- Dataset: {ds.get('dataset_id') or '-'} (historical snapshot, not live)",
        f"- Snapshot: `{ds.get('snapshot_id') or '-'}`",
        f"- Data cutoff: {ds.get('data_cutoff') or '-'}; last complete day {ds.get('last_complete_date') or '-'}",
        f"- Coverage: {ds.get('first_date') or '-'} to {ds.get('last_complete_date') or '-'}",
    ]
    if report.get("periods"):
        lines.append(
            "- Periods: "
            + "; ".join(f"{p.get('label', '')} {p.get('start')} to {p.get('end')}" for p in report["periods"])
        )
    lines += ["", "## Verified answer", ""]
    lines.append(report["verified_answer"] or f"_No verified answer._ {report.get('status_message') or ''}".strip())
    lines += ["", "## Verified claims", ""]
    rows = _claim_rows(report)
    lines += _md_table(CLAIM_HEADERS, rows) if rows else ["_No quantitative claims._"]
    for t in report.get("tables") or []:
        headers, trows = _table_rows(t)
        if headers and trows:
            lines += ["", f"### {t.get('title') or 'Table'}", "", *_md_table(headers, trows)]
    lines += ["", "## Evidence", ""]
    for e in report["evidence"]:
        lines += [
            f"### {e['evidence_id']} · `{e['tool']}` ({e['status']}, {e['capability']})",
            "",
            e.get("summary") or "",
            "",
            f"- Arguments: `{json.dumps(e.get('arguments') or {}, sort_keys=True)}`",
            f"- Snapshot: `{e.get('data_build_id') or '-'}`; cutoff {e.get('data_cutoff') or '-'}",
            f"- Checksum (SHA-256): `{e['checksum']}`",
        ]
        if e.get("model_version"):
            lines.append(f"- Model: {e.get('model_name') or '-'} version `{e['model_version']}`")
        lines.append("")
    if report.get("models"):
        lines += ["## Models", ""]
        lines += _md_table(
            ["Evidence", "Tool", "Capability", "Model", "Version"],
            [
                [
                    m["evidence_id"],
                    m["tool"],
                    m["capability"],
                    m.get("model_name") or "-",
                    m.get("model_version") or "-",
                ]
                for m in report["models"]
            ],
        )
        lines.append("")
    if report.get("warnings"):
        lines += ["## Warnings", "", *[f"- **{w['level']}:** {w['message']}" for w in report["warnings"]], ""]
    lines += ["## Limitations", "", *[f"- {x}" for x in report["limitations"]], ""]
    lines += ["## MCP tools used", ""]
    lines += _md_table(
        ["Step", "Tool", "Status", "Evidence", "ms"],
        [
            [str(t["step"]), t["tool"], t["status"], t.get("evidence_id") or "-", f"{t['duration_ms']:.0f}"]
            for t in report["mcp"]["tools_used"]
        ],
    )
    prov = report["provider"]
    lines += [
        "",
        "## Method",
        "",
        f"- Provider: {prov['name']} ({_mode(prov)})",
        f"- MCP: {report['mcp'].get('server') or '-'} {report['mcp'].get('server_version') or ''}, protocol "
        f"{report['mcp'].get('protocol_version') or '-'}, transport {report['mcp'].get('transport') or '-'}",
        f"- Validation: {report['validation']['method']}",
        "",
    ]
    return "\n".join(lines)


_PRINT_CSS = """
body{font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#111;background:#fff;max-width:960px;
margin:24px auto;padding:0 16px}h1{font-size:22px;margin:0 0 8px}h2{font-size:17px;margin:24px 0 8px;
border-bottom:1px solid #ccc;padding-bottom:4px}h3{font-size:15px;margin:16px 0 4px}table{border-collapse:collapse;
width:100%;margin:8px 0;font-size:12.5px}th,td{border:1px solid #bbb;padding:4px 6px;text-align:left;vertical-align:top}
th{background:#f1f3f6}code{font:12px ui-monospace,Consolas,monospace;word-break:break-all}.meta{color:#444}
.answer{white-space:pre-wrap;border-left:3px solid #0f766e;padding:6px 10px;background:#f6faf9}
.note{color:#7a4b00}@media print{body{margin:0;max-width:none}h2{break-after:avoid}table,tr{break-inside:avoid}}
"""


DEMO_BANNER = "Demonstration Mode — deterministic analytical assistant, not a live LLM."


def _mode(prov: dict[str, Any]) -> str:
    return "live LLM" if prov.get("live_llm") else "deterministic demonstration provider, not a live LLM"


def _verified(report: dict[str, Any]) -> str:
    return "verified" if report.get("answer_verified") else "no verified answer"


def _h(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def _html_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f'<th scope="col">{_h(c)}</th>' for c in headers)
    body = "".join("<tr>" + "".join(f"<td>{_h(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_html(report: dict[str, Any]) -> str:
    """Self-contained, script-free, print-friendly HTML. Every value is escaped."""
    ds = report["dataset"]
    prov = report["provider"]
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">",
        f"<title>{_h(report['title'])}</title><style>{_PRINT_CSS}</style></head><body><main>",
        f"<h1>{_h(report['title'])}</h1>",
        f'<p class="meta">Question: {_h(report["question"])}<br>Status: {_h(report["status"])} '
        f"({_verified(report)}) · Generated {_h(report['generated_at'])}"
        f"<br>Report {_h(report['report_id'])} · schema <code>{_h(report['schema_version'])}</code>"
        f"<br>Content checksum (SHA-256): <code>{_h(report['content_checksum'])}</code></p>",
    ]
    if not prov["live_llm"]:
        parts.append(f'<p class="note"><strong>{DEMO_BANNER}</strong></p>')
    parts += [
        "<h2>Data</h2><ul>",
        f"<li>Dataset: {_h(ds.get('dataset_id') or '-')} (historical snapshot, not live)</li>",
        f"<li>Snapshot: <code>{_h(ds.get('snapshot_id') or '-')}</code></li>",
        f"<li>Data cutoff: {_h(ds.get('data_cutoff') or '-')}; "
        f"last complete day {_h(ds.get('last_complete_date'))}</li>",
        "</ul><h2>Verified answer</h2>",
    ]
    if report["verified_answer"]:
        parts.append(f'<div class="answer">{_h(report["verified_answer"])}</div>')
    else:
        parts.append(f"<p><em>No verified answer.</em> {_h(report.get('status_message') or '')}</p>")
    rows = _claim_rows(report)
    parts.append("<h2>Verified claims</h2>")
    parts.append(_html_table(CLAIM_HEADERS, rows) if rows else "<p><em>No quantitative claims.</em></p>")
    for t in report.get("tables") or []:
        headers, trows = _table_rows(t)
        if headers and trows:
            parts.append(f"<h3>{_h(t.get('title') or 'Table')}</h3>{_html_table(headers, trows)}")
    parts.append("<h2>Evidence</h2>")
    for e in report["evidence"]:
        parts.append(
            f"<h3>{_h(e['evidence_id'])} · <code>{_h(e['tool'])}</code> ({_h(e['status'])}, {_h(e['capability'])})</h3>"
            f"<p>{_h(e.get('summary'))}</p><ul>"
            f"<li>Arguments: <code>{_h(json.dumps(e.get('arguments') or {}, sort_keys=True))}</code></li>"
            f"<li>Snapshot <code>{_h(e.get('data_build_id') or '-')}</code>; "
            f"cutoff {_h(e.get('data_cutoff') or '-')}</li>"
            + (
                f"<li>Model {_h(e.get('model_name'))} version <code>{_h(e['model_version'])}</code></li>"
                if e.get("model_version")
                else ""
            )
            + f"<li>Checksum (SHA-256): <code>{_h(e['checksum'])}</code></li></ul>"
        )
    if report.get("warnings"):
        parts.append(
            "<h2>Warnings</h2><ul>"
            + "".join(f"<li><strong>{_h(w['level'])}:</strong> {_h(w['message'])}</li>" for w in report["warnings"])
            + "</ul>"
        )
    parts.append("<h2>Limitations</h2><ul>" + "".join(f"<li>{_h(x)}</li>" for x in report["limitations"]) + "</ul>")
    parts.append("<h2>MCP tools used</h2>")
    parts.append(
        _html_table(
            ["Step", "Tool", "Status", "Evidence", "ms"],
            [
                [str(t["step"]), t["tool"], t["status"], t.get("evidence_id") or "-", f"{t['duration_ms']:.0f}"]
                for t in report["mcp"]["tools_used"]
            ],
        )
    )
    parts.append(
        "<h2>Method</h2><ul>"
        f"<li>Provider: {_h(prov['name'])} ({_mode(prov)})</li>"
        f"<li>MCP protocol {_h(report['mcp'].get('protocol_version') or '-')}, "
        f"transport {_h(report['mcp'].get('transport') or '-')}</li>"
        f"<li>{_h(report['validation']['method'])}</li></ul></main></body></html>"
    )
    return "".join(parts)


# ---------------------------------------------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "verify":
        print("usage: python -m civicpulse_agent.report verify <report.json>", file=sys.stderr)
        return 2
    report = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    out = verify_report(report)
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
