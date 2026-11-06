"""Deterministic grounding checks for generated answers.

Checks (no LLM judge):
* every cited evidence ID exists;
* every number in the answer (except dates, years and short window lengths) appears, within the
  rounding shown, in an evidence item cited in the same sentence or line (fractions may be shown as
  percentages);
* a quantitative answer cites at least one evidence item;
* policy phrases: no confirmed-incident language for anomaly flags, no live-monitoring claims, no
  causal claims, no universal-improvement claims for the forecast, mandatory restriction / uncertainty
  wording when experimental or disabled capabilities are used.

Passing these checks means each number points to a verifiable computation. It does not guarantee the
prose interprets that computation correctly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from civicpulse_agent.evidence import Evidence, numbers_in

_CITATION = re.compile(r"\[(E\d{1,2}(?:\s*,\s*E\d{1,2})*)\]")
_CITE_ID = re.compile(r"E\d{1,2}")
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_DATES = [
    re.compile(r"\b(?:[a-z][a-z_]*(?:-[a-z_]+)*-)?\d{8}T\d{6}Z(?:-[0-9a-f]{4,8})?\b", re.I),  # build/model run ids
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b"),
    re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b", re.I),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\b(?:\s+\d{{4}}\b)?", re.I),
    re.compile(rf"\b{_MONTH}\.?\s+\d{{4}}\b", re.I),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),  # clock times
    re.compile(r"\bNYC 311\b|\b311(?= (service|request|complaint|call|data|snapshot))", re.I),  # the service name
]
_NUMBER = re.compile(r"(?<![\w.])(-?\d{1,3}(?:,\d{3})+|-?\d+)(\.\d+)?(\s?%)?")
_YEAR = re.compile(r"^(19|20)\d{2}$")
_WINDOW_UNIT = re.compile(
    r"^\s?-?\s?(complete |calendar |same[- ]weekday |previous |prior )?(day|days|week|weeks|month|months|hour|hours|"
    r"year|years|weekdays)\b",
    re.I,
)
_NEGATION = re.compile(r"\b(not|no|cannot|can't|isn't|doesn't|don't|without|never|nor|unable)\b", re.I)
_CAUSAL = re.compile(r"\b(caused by|because of|due to|led to|leads to|resulted in|driven by|was triggered by)\b", re.I)
_CONFIRMED = re.compile(
    r"\b(confirmed (incident|outage|event|emergency)s?|(was|were) (an? )?(real )?incidents?)\b", re.I
)
_LIVE = re.compile(r"\b(real[- ]time|live (data|feed|monitoring)|as of (right )?now|currently happening)\b", re.I)
_UNIVERSAL = re.compile(
    r"\b(outperforms?|beats?|better than|more accurate than)\s+(all|every|each)\s+(of the )?(baselines?|models?)\b",
    re.I,
)
_NAIVE_SIGNIFICANT = re.compile(
    r"\bsignificantly (better|more accurate) than (the )?(plain |ordinary |simple )?naive\b", re.I
)


@dataclass
class NumberCheck:
    token: str
    sentence: str
    cited: list[str]
    status: str  # supported | unsupported | uncited | miscited
    matched_evidence: str | None = None


@dataclass
class GroundingReport:
    ok: bool
    citations_used: list[str]
    invalid_citations: list[str]
    numbers_checked: int
    number_checks: list[NumberCheck] = field(default_factory=list)
    policy_violations: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[NumberCheck]:
        return [c for c in self.number_checks if c.status != "supported"]

    def feedback(self) -> str:
        lines = []
        if self.invalid_citations:
            lines.append(f"These evidence IDs do not exist: {', '.join(self.invalid_citations)}.")
        for c in self.failures[:10]:
            if c.status == "uncited":
                lines.append(f"The number {c.token} has no [E#] citation in its sentence.")
            elif c.status == "miscited":
                lines.append(
                    f"The number {c.token} is not in the cited evidence {c.cited}; it is in {c.matched_evidence}."
                )
            else:
                lines.append(f"The number {c.token} does not appear in any evidence. Remove it or use a tool value.")
        lines += [f"Policy: {v}" for v in self.policy_violations]
        return " ".join(lines)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "citations_used": self.citations_used,
            "invalid_citations": self.invalid_citations,
            "numbers_checked": self.numbers_checked,
            "unsupported_numbers": [c.token for c in self.failures],
            "number_checks": [c.__dict__ for c in self.number_checks[:60]],
            "policy_violations": self.policy_violations,
        }


def _mask_dates(text: str) -> str:
    for rx in _DATES:
        text = rx.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _split_units(answer: str) -> list[tuple[str, str]]:
    """(sentence, enclosing line) pairs. Citations may sit anywhere in the sentence or its line."""
    units = []
    for line in answer.splitlines():
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z\[(*-])", line):
            if sent.strip():
                units.append((sent, line))
    return units


def _supported(value: float, decimals: int, is_pct: bool, pool: set[float]) -> bool:
    tol = 0.5 * 10 ** (-decimals) + 1e-9
    for v in pool:
        if abs(value - v) <= tol:
            return True
        if abs(value - 100 * v) <= tol and (is_pct or decimals > 0 or abs(v) <= 1):
            return True
    return False


def free_numbers(text: str) -> list[str]:
    """Numeric tokens that are not dates, clock times, years, short window lengths or the 311 service name."""
    masked = _mask_dates(_CITATION.sub(lambda m: " " * len(m.group(0)), text))
    out = []
    for m in _NUMBER.finditer(masked):
        int_part, frac, pct = m.group(1), m.group(2) or "", m.group(3) or ""
        raw = int_part.replace(",", "")
        if not frac and not pct and _YEAR.match(raw.lstrip("-")):
            continue
        value = float(raw + frac)
        if not frac and not pct and 0 < value <= 31 and _WINDOW_UNIT.match(masked[m.end() :]):
            continue
        out.append((int_part + frac + pct.strip()).strip())
    return out


def validate_answer(
    answer: str,
    evidence: dict[str, Evidence],
    question: str = "",
    extra_numbers: dict[str, set[float]] | None = None,
) -> GroundingReport:
    cited_all = sorted({c for grp in _CITATION.findall(answer) for c in _CITE_ID.findall(grp)})
    invalid = [c for c in cited_all if c not in evidence]
    pools = {eid: numbers_in(ev.result) | numbers_in(ev.arguments) for eid, ev in evidence.items()}
    for eid, extra in (extra_numbers or {}).items():
        if eid in pools:
            pools[eid] = pools[eid] | extra
    q_numbers = numbers_in(question)
    checks: list[NumberCheck] = []
    violations: list[str] = []

    for sent, line in _split_units(answer):
        cites = [c for grp in _CITATION.findall(sent) for c in _CITE_ID.findall(grp)]
        if not cites:
            cites = [c for grp in _CITATION.findall(line) for c in _CITE_ID.findall(grp)]
        masked = _mask_dates(_CITATION.sub(lambda m: " " * len(m.group(0)), sent))
        for m in _NUMBER.finditer(masked):
            int_part, frac, pct = m.group(1), m.group(2) or "", m.group(3) or ""
            token = (int_part + frac + pct.strip()).strip()
            raw = int_part.replace(",", "")
            if not frac and not pct and _YEAR.match(raw.lstrip("-")):
                continue  # a year
            value = float(raw + frac)
            after = masked[m.end() :]
            if not frac and not pct and 0 < value <= 31 and _WINDOW_UNIT.match(after):
                continue  # a window length such as "7-day", "28 complete days" or "3 weeks"
            if value in q_numbers:
                continue  # echoes the user's own number
            decimals = len(frac) - 1 if frac else 0
            valid_cites = [c for c in cites if c in evidence]
            if not valid_cites:
                status, matched = (
                    "uncited",
                    next((eid for eid, pool in pools.items() if _supported(value, decimals, bool(pct), pool)), None),
                )
            else:
                hit = next((c for c in valid_cites if _supported(value, decimals, bool(pct), pools[c])), None)
                if hit:
                    status, matched = "supported", hit
                else:
                    other = next(
                        (eid for eid, pool in pools.items() if _supported(value, decimals, bool(pct), pool)), None
                    )
                    status, matched = ("miscited", other) if other else ("unsupported", None)
            checks.append(NumberCheck(token, sent.strip()[:200], valid_cites, status, matched))

        has_neg = bool(_NEGATION.search(sent))
        if (
            _CAUSAL.search(sent)
            and not has_neg
            and not re.search(r"\blimitation|caveat|label drift|censor", sent, re.I)
        ):
            violations.append(f"causal claim not supported by any tool: '{sent.strip()[:120]}'")
        if _CONFIRMED.search(sent) and not has_neg:
            violations.append("anomaly flags must not be described as confirmed incidents")
        if _LIVE.search(sent) and not has_neg:
            violations.append("the data is a historical snapshot; do not describe it as live or real-time")
        if _UNIVERSAL.search(sent) and not has_neg:
            violations.append("do not claim the forecast beats every baseline")
        if _NAIVE_SIGNIFICANT.search(sent) and not has_neg:
            violations.append("the forecast is NOT significantly better than the naive baseline")

    tools_used = {ev.tool for ev in evidence.values() if ev.evidence_id in cited_all}
    caps = {ev.capability for ev in evidence.values() if ev.evidence_id in cited_all}
    low = answer.lower()
    if checks and not cited_all:
        violations.append("a quantitative answer must cite evidence")
    if "DISABLED" in caps and not re.search(r"not validated|disabled|experimental|restricted", low):
        violations.append("answers using the closure-delay model must state it is experimental / not validated")
    if "detect_unusual_spikes" in tools_used and not re.search(r"statistical|unverified|not confirmed", low):
        violations.append("anomaly answers must say flags are statistical / unverified")
    if "forecast_request_volume" in tools_used and not re.search(r"interval|uncertain", low):
        violations.append("forecast answers must include uncertainty (interval) information")

    ok = not invalid and not violations and all(c.status == "supported" for c in checks)
    return GroundingReport(ok, cited_all, invalid, len(checks), checks, violations)
