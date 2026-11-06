# Semantic grounding of AI Analyst answers (Milestone 4)

Status: **implemented and tested with the deterministic fake provider.** Not verified with a live LLM.
Code: `packages/agent/civicpulse_agent/claims.py`, `report.py`. Tests: `packages/agent/tests/test_semantic_claims.py`,
`test_report.py`, `test_orchestrator.py`.

## Pipeline

```
MCP tool result ──► Evidence (E1…, snapshot id, cutoff, model version)
                 ──► Facts (metric, geography, category, period, comparison period, unit, value, snapshot,
                            model version, method)            extract_facts / build_fact_index (conflicts)
provider answer  ──► JSON {"answer": "...{C1}...", "claims": [...]}
                 ──► ClaimValidator: every claim matched to one fact, or recomputed (derived)
                 ──► narrative checks: no free numbers in prose; prose geography/category/period, direction
                     words and superlatives must agree with the referenced claims
                 ──► render: values come from facts, never from provider text; citations [E#] appended
                 ──► legacy number/citation/policy validator on the rendered text (defense in depth)
```

A failing answer is regenerated once with the validator's feedback, then replaced by a verified summary built
from the evidence, or by an explicit statement that the evidence is insufficient.

## What a claim is checked against

| Field | Rule |
|---|---|
| evidence_id | must exist in this investigation (invented IDs fail) |
| metric, geography, category | must equal the fact's (right value with the wrong borough or category fails) |
| period, comparison period | must equal the fact's dates |
| unit | must be compatible (`fraction` may be stated as `percent`) |
| value | exact for counts; within the precision stated for rates/fractions |
| snapshot_id | required and must equal the evidence snapshot (missing or different snapshot fails) |
| model_version | must equal the evidence and the current version from `civicpulse://dataset` (stale fails) |
| method | must match the tool's calculation method when given |
| conflicts | facts with the same key but different values across evidence cannot be cited |
| derived (`pct_change`, `difference`, `share`, `ratio`) | recomputed from validated operand claims; wrong denominator or arithmetic fails |
| comparison | relation (`greater`/`less`/`equal`) recomputed; wrong direction fails |
| causal language | refused by the policy validator (data cannot establish cause) |

## Verified behaviour

* 31 unit tests cover each rule above, including wrong borough/category/period/unit, wrong denominator,
  wrong comparison direction, causal claims, missing citation, invented evidence ID, conflicting evidence, stale
  model version and missing snapshot.
* Agent evaluation (fake provider, real stdio MCP): 10 `semantic_grounding` scenarios inject those faults into
  the first answer; all 9 fault scenarios are caught on the first attempt and corrected or replaced; 70/70
  scenarios pass overall (`AGENT_EVAL.md`).
* Reports (`report.py`) contain only validated claims; `verify_report` re-extracts facts from the stored evidence
  and re-validates every claim offline without the LLM, detecting tampered values and evidence (SHA-256).

## Limitations

* The fake provider produces well-formed claims by construction; how often a real model produces valid claims on
  the first attempt is **unknown** until an authorized live run.
* Narrative checks are lexical (borough names, category words, dates, direction words). Paraphrases that avoid
  those words are not interpreted; numbers in prose are always rejected, so the residual risk is wording, not values.
* Claims are validated against tool outputs. Whether a tool was the right one for the question is checked by the
  evaluation scenarios, not at runtime.
