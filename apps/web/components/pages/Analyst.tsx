"use client";

import { Fragment, type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { TimeSeriesChart } from "@/components/charts";
import { ApiError, postJson, useApi } from "@/hooks/useApi";
import type { AnalystStatus, AnalystWarning, ChartSpec, EvidenceItem, Investigation, TraceStep } from "@/lib/ai-types";
import { SERIES_COLORS, fmtInt } from "@/lib/format";

export const SUGGESTED_QUESTIONS = [
  "Which borough had the highest residential noise complaint volume last month?",
  "How did Brooklyn compare with Queens?",
  "Which days had the largest complaint spikes?",
  "What does the model forecast for next week relative to the dataset cutoff?",
  "Is the forecast more accurate than seasonal naive?",
  "How reliable is the delay-risk model?",
  "Why shouldn't we use the closure-risk model to allocate resources?",
  "What anomalies were detected around heating-season onset?",
  "How fresh is the data?",
  "Which data-quality limitations affect the analysis?",
];

interface Turn {
  id: string;
  question: string;
  state: "loading" | "done" | "error";
  result?: Investigation;
  error?: ApiError;
}

const STATUS_LABEL: Record<string, { label: string; color: string; icon: string }> = {
  answered: { label: "Answered · evidence check passed", color: "var(--status-good)", icon: "M5 12l4 4 10-10" },
  clarification: { label: "Needs clarification", color: "var(--accent-cyan)", icon: "M9 9a3 3 0 1 1 4 2.8c-.6.3-1 .9-1 1.6V14M12 17h.01" },
  refused: { label: "Declined by policy", color: "var(--status-warning)", icon: "M5 5l14 14M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z" },
  fallback: { label: "Verified summary (generated answer failed checks)", color: "var(--status-warning)", icon: "M12 4 2 20h20L12 4zM12 10v4M12 17h.01" },
  error: { label: "Not answered", color: "var(--status-critical)", icon: "M6 6l12 12M18 6 6 18" },
};

const STEP_COLOR: Record<string, string> = {
  ok: "var(--status-good)", passed: "var(--status-good)", restricted: "var(--status-warning)",
  no_data: "var(--status-warning)", rejected: "var(--status-critical)", error: "var(--status-critical)",
  failed: "var(--status-critical)", skipped: "var(--text-muted)",
};

function Icon({ d, color = "currentColor", size = 14 }: { d: string; color?: string; size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.5"
      strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
  );
}

function Button({ children, onClick, disabled, variant = "ghost", label, type = "button" }: {
  children: ReactNode; onClick?: () => void; disabled?: boolean; variant?: "primary" | "ghost"; label?: string;
  type?: "button" | "submit";
}) {
  const style = variant === "primary"
    ? "bg-[color:var(--accent-cyan)] text-[#04121f] font-semibold hover:brightness-110 disabled:opacity-50"
    : "border border-border-strong text-ink hover:bg-surface-2 disabled:opacity-50";
  return (
    <button type={type} onClick={onClick} disabled={disabled} aria-label={label}
      className={`inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 py-1.5 text-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--accent-cyan)] ${style}`}>
      {children}
    </button>
  );
}

// ---------------------------------------------------------------- answer rendering (no HTML injection)
function inline(text: string, onCite: (id: string) => void, key: string): ReactNode[] {
  const parts = text.split(/(\[E\d{1,2}(?:\s*,\s*E\d{1,2})*\]|\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((p, i) => {
    const k = `${key}-${i}`;
    if (/^\[E\d/.test(p)) {
      const ids = p.slice(1, -1).split(/\s*,\s*/);
      return (
        <span key={k} className="whitespace-nowrap">
          {ids.map((id) => (
            <button key={id} type="button" onClick={() => onCite(id)} aria-label={`Show evidence ${id}`}
              className="mx-0.5 rounded border border-[color:var(--accent-cyan)]/50 px-1 text-[10px] font-semibold text-[color:var(--accent-cyan)] hover:bg-[color:var(--accent-cyan)]/10 focus-visible:outline-2 focus-visible:outline-[color:var(--accent-cyan)]">
              {id}
            </button>
          ))}
        </span>
      );
    }
    if (p.startsWith("**") && p.endsWith("**")) return <strong key={k} className="text-ink">{p.slice(2, -2)}</strong>;
    if (p.startsWith("`") && p.endsWith("`")) return <code key={k} className="rounded bg-surface-2 px-1 text-[12px]">{p.slice(1, -1)}</code>;
    return <Fragment key={k}>{p}</Fragment>;
  });
}

function AnswerText({ text, onCite }: { text: string; onCite: (id: string) => void }) {
  const blocks: ReactNode[] = [];
  let bullets: { text: string; nested: boolean }[] = [];
  const flush = (k: number) => {
    if (!bullets.length) return;
    blocks.push(
      <ul key={`ul-${k}`} className="my-2 space-y-1.5 pl-5 text-sm leading-relaxed text-ink-2 marker:text-ink-3 list-disc">
        {bullets.map((b, i) => <li key={i} className={b.nested ? "ml-5 list-[circle]" : ""}>{inline(b.text, onCite, `b${k}-${i}`)}</li>)}
      </ul>,
    );
    bullets = [];
  };
  text.split("\n").forEach((line, i) => {
    const m = /^(\s*)[-*]\s+(.*)$/.exec(line);
    if (m) {
      bullets.push({ text: m[2], nested: m[1].length > 0 });
      return;
    }
    flush(i);
    if (line.trim()) blocks.push(<p key={`p-${i}`} className="my-2 text-sm leading-relaxed text-ink-2">{inline(line, onCite, `p${i}`)}</p>);
  });
  flush(-1);
  return <div>{blocks}</div>;
}

// ---------------------------------------------------------------- panels
function Warnings({ warnings }: { warnings: AnalystWarning[] }) {
  if (!warnings.length) return null;
  return (
    <div role="note" aria-label="Model warnings" className="mt-3 space-y-2">
      {warnings.map((w, i) => {
        const restricted = w.level === "restricted";
        return (
          <div key={i} className={`flex gap-2 rounded-lg border p-3 text-xs ${restricted
            ? "border-[color:var(--status-critical)]/50 bg-[color:var(--status-critical)]/10"
            : "border-[color:var(--status-warning)]/50 bg-[color:var(--status-warning)]/10"}`}>
            <span className="mt-0.5"><Icon d="M12 4 2 20h20L12 4zM12 10v4M12 17h.01" color={restricted ? "var(--status-critical)" : "var(--status-warning)"} /></span>
            <p className="text-ink-2">
              <span className="font-semibold text-ink">{restricted ? "Restricted model" : "Experimental model"}
                {w.capability ? ` (${w.capability})` : ""}{w.tool ? ` · ${w.tool}` : ""}: </span>
              {w.message}
            </p>
          </div>
        );
      })}
    </div>
  );
}

function EvidenceCard({ ev, open, onToggle, cardRef }: {
  ev: EvidenceItem; open: boolean; onToggle: () => void; cardRef: (el: HTMLDivElement | null) => void;
}) {
  const panelId = useId();
  return (
    <div ref={cardRef} tabIndex={-1} id={`evidence-${ev.evidence_id}`}
      className="rounded-lg border border-border bg-surface-2/40 focus:outline-2 focus:outline-[color:var(--accent-cyan)]">
      <button type="button" aria-expanded={open} aria-controls={panelId} onClick={onToggle}
        className="flex w-full items-start gap-2 p-3 text-left text-xs hover:bg-surface-2">
        <span className="rounded border border-[color:var(--accent-cyan)]/50 px-1 font-semibold text-[color:var(--accent-cyan)]">{ev.evidence_id}</span>
        <span className="min-w-0 flex-1">
          <span className="font-mono text-ink">{ev.tool}</span>
          <span className="ml-2 text-ink-3">{ev.capability} · {ev.status}</span>
          <span className="mt-0.5 block text-ink-2">{ev.summary}</span>
        </span>
        <span className="text-ink-3" aria-hidden="true">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div id={panelId} className="space-y-3 border-t border-border p-3 text-xs text-ink-2">
          <dl className="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-2">
            <div><dt className="inline text-ink-3">Arguments: </dt><dd className="inline break-all font-mono">{JSON.stringify(ev.arguments)}</dd></div>
            <div><dt className="inline text-ink-3">Data build: </dt><dd className="inline font-mono">{ev.data_build_id ?? "—"}</dd></div>
            <div><dt className="inline text-ink-3">Data cutoff: </dt><dd className="inline">{ev.data_cutoff ?? "—"} (NYC)</dd></div>
            <div><dt className="inline text-ink-3">Model version: </dt><dd className="inline font-mono">{ev.model_version ?? "none (warehouse query)"}</dd></div>
            {ev.artifact && <div><dt className="inline text-ink-3">Artifact: </dt><dd className="inline">{ev.artifact}</dd></div>}
            {ev.coverage?.note && <div className="sm:col-span-2"><dt className="inline text-ink-3">Coverage: </dt><dd className="inline">{ev.coverage.note}</dd></div>}
          </dl>
          {ev.key_measurements.length > 0 && (
            <div className="max-h-56 overflow-auto">
              <table className="w-full table-fixed text-left">
                <caption className="sr-only">Key measurements for {ev.evidence_id}</caption>
                <thead className="text-ink-3"><tr><th scope="col" className="w-[55%] pr-3 font-medium">Measurement</th><th scope="col" className="pr-3 font-medium">Value</th><th scope="col" className="w-[20%] font-medium">Unit</th></tr></thead>
                <tbody className="tabular">
                  {ev.key_measurements.slice(0, 30).map((m, i) => (
                    <tr key={i} className="border-t border-border"><td className="break-all py-1 pr-3 font-mono">{m.name}</td><td className="py-1 pr-3">{typeof m.value === "number" ? m.value.toLocaleString("en-US") : String(m.value)}</td><td className="py-1">{m.unit ?? ""}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {Object.keys(ev.metric_definitions).length > 0 && (
            <div><p className="font-medium text-ink">Metric definitions</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">{Object.entries(ev.metric_definitions).map(([k, v]) => <li key={k}><span className="font-mono">{k}</span>: {v}</li>)}</ul></div>
          )}
          {[...ev.uncertainty, ...ev.limitations].length > 0 && (
            <div><p className="font-medium text-ink">Uncertainty and limitations</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">{[...ev.uncertainty, ...ev.limitations].map((l, i) => <li key={i}>{l}</li>)}</ul></div>
          )}
        </div>
      )}
    </div>
  );
}

function AnalystChart({ chart }: { chart: ChartSpec }) {
  const points = chart.series.flatMap((s) => s.points.filter((p) => p.y != null).map((p) => ({ period: p.x, key: s.name, value: p.y as number })));
  const colors = Object.fromEntries(chart.series.map((s, i) => [s.name, SERIES_COLORS[i % SERIES_COLORS.length]]));
  const labels = Object.fromEntries(chart.series.map((s) => [s.name, s.name]));
  const isDate = /^\d{4}-\d{2}-\d{2}$/.test(chart.series[0]?.points[0]?.x ?? "");
  return (
    <figure className="rounded-lg border border-border p-3">
      <figcaption className="mb-1 text-xs text-ink-2">{chart.title} <span className="text-ink-3">[{chart.evidence_id}]</span></figcaption>
      <TimeSeriesChart points={points} colors={colors} labels={labels} kind={chart.kind} label={chart.title}
        periodFormat={isDate ? undefined : (s: string) => s} />
    </figure>
  );
}

function TracePanel({ result }: { result?: Investigation }) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [all, setAll] = useState(false);
  if (!result) {
    return <p className="text-xs text-ink-3">The trace of tool calls, validation and policy checks for the selected answer appears here.</p>;
  }
  const attempts = result.validation.attempts ?? [];
  const last = attempts[attempts.length - 1];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] text-ink-3">
          MCP {result.mcp.server ?? "—"} {result.mcp.server_version ?? ""} · protocol {result.mcp.protocol_version ?? "—"} · {result.mcp.transport ?? ""}
        </p>
        <Button onClick={() => setAll((v) => !v)}>{all ? "Collapse all" : "Expand trace"}</Button>
      </div>
      <ol className="space-y-1.5" aria-label="Investigation trace">
        {result.trace.map((t: TraceStep) => {
          const open = all || expanded[t.step];
          return (
            <li key={t.step} className="rounded-md border border-border">
              <button type="button" aria-expanded={!!open} onClick={() => setExpanded((e) => ({ ...e, [t.step]: !e[t.step] }))}
                className="flex w-full items-center gap-2 px-2.5 py-2 text-left text-xs hover:bg-surface-2">
                <Icon d={t.status === "ok" || t.status === "passed" ? "M5 12l4 4 10-10" : t.status === "skipped" ? "M5 12h14" : "M12 8v5M12 16h.01"} color={STEP_COLOR[t.status] ?? "var(--text-muted)"} />
                <span className="text-ink-3">{t.kind}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-ink">{t.name}</span>
                <span className="text-ink-2">{t.status}</span>
                {t.evidence_id && <span className="font-semibold text-[color:var(--accent-cyan)]">{t.evidence_id}</span>}
                <span className="tabular text-ink-3">{t.duration_ms ? `${t.duration_ms.toFixed(0)} ms` : ""}</span>
              </button>
              {open && (
                <dl className="space-y-0.5 border-t border-border px-2.5 py-2 text-[11px] text-ink-2">
                  {t.arguments && <div><dt className="inline text-ink-3">Arguments (sanitized): </dt><dd className="inline break-all font-mono">{JSON.stringify(t.arguments)}</dd></div>}
                  {t.data_source && <div><dt className="inline text-ink-3">Data source: </dt><dd className="inline">{t.data_source}</dd></div>}
                  {t.model_version && <div><dt className="inline text-ink-3">Model version: </dt><dd className="inline font-mono">{t.model_version}</dd></div>}
                  {t.capability && <div><dt className="inline text-ink-3">Capability: </dt><dd className="inline">{t.capability}</dd></div>}
                  {t.error_kind && <div><dt className="inline text-ink-3">Error kind: </dt><dd className="inline">{t.error_kind}</dd></div>}
                  {t.message && <div><dt className="inline text-ink-3">Detail: </dt><dd className="inline">{t.message}</dd></div>}
                </dl>
              )}
            </li>
          );
        })}
      </ol>
      <div className="rounded-md border border-border p-2.5 text-[11px] text-ink-2">
        <p className="font-medium text-ink">Validation</p>
        {last ? (
          <p>{last.ok ? "Passed" : "Failed"}: {last.numbers_checked} numbers checked against cited evidence
            {last.citations_used.length ? ` (${last.citations_used.join(", ")})` : ""}; {result.validation.regenerations ?? 0} regeneration(s).
            {!last.ok && last.unsupported_numbers.length > 0 && ` Unsupported: ${last.unsupported_numbers.join(", ")}.`}
            {!last.ok && last.policy_violations.length > 0 && ` Policy: ${last.policy_violations.join("; ")}.`}
            {result.validation.fallback && ` Fallback summary check: ${result.validation.fallback.ok ? "passed" : "failed"}.`}</p>
        ) : <p>{result.validation.screening ? `Input screening: ${result.validation.screening}` : "No answer was validated."}</p>}
        <p className="mt-1 text-ink-3">
          {result.usage.tool_calls} tool call(s) · {result.usage.provider_calls} provider call(s) · {fmtInt(result.usage.input_tokens)} in / {fmtInt(result.usage.output_tokens)} out tokens · {(result.duration_ms / 1000).toFixed(1)} s
        </p>
      </div>
    </div>
  );
}

function TurnView({ turn, selected, onSelect, onRetry }: {
  turn: Turn; selected: boolean; onSelect: () => void; onRetry: () => void;
}) {
  const [openEvidence, setOpenEvidence] = useState<Record<string, boolean>>({});
  const [showEvidence, setShowEvidence] = useState(false);
  const [copied, setCopied] = useState(false);
  const refs = useRef<Record<string, HTMLDivElement | null>>({});
  const r = turn.result;

  const cite = (id: string) => {
    onSelect();
    setShowEvidence(true);
    setOpenEvidence((o) => ({ ...o, [id]: true }));
    requestAnimationFrame(() => {
      const el = refs.current[id];
      el?.focus();
      el?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    });
  };
  const copy = async () => {
    if (!r) return;
    const text = `Q: ${r.question}\n\n${r.answer}\n\nEvidence:\n${r.evidence.map((e) => `[${e.evidence_id}] ${e.tool}: ${e.summary}`).join("\n")}\n\nData cutoff ${r.freshness.data_cutoff ?? "?"} (snapshot, not live).`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <article aria-label={`Question: ${turn.question}`}
      className={`rounded-xl border bg-surface-1 p-4 sm:p-5 ${selected ? "border-[color:var(--accent-cyan)]/60" : "border-border"}`}
      onClick={onSelect}>
      <p className="text-sm font-medium text-ink"><span className="text-ink-3">Q · </span>{turn.question}</p>
      {turn.state === "loading" && (
        <div role="status" aria-live="polite" className="mt-3 space-y-2">
          <p className="text-xs text-ink-3">Investigating: calling MCP tools and checking evidence…</p>
          <div className="skeleton h-4 w-11/12" aria-hidden="true" /><div className="skeleton h-4 w-9/12" aria-hidden="true" /><div className="skeleton h-4 w-10/12" aria-hidden="true" />
        </div>
      )}
      {turn.state === "error" && turn.error && (
        <div role="alert" className="mt-3 rounded-lg border border-[color:var(--status-critical)]/50 bg-[color:var(--status-critical)]/10 p-3 text-sm">
          <p className="font-medium text-ink">The analyst couldn’t answer</p>
          <p className="mt-1 text-ink-2">{turn.error.message}</p>
          <div className="mt-2"><Button onClick={onRetry}>Retry</Button></div>
        </div>
      )}
      {r && (
        <div className="mt-3" aria-live="polite">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-xs text-ink">
              <Icon d={STATUS_LABEL[r.status].icon} color={STATUS_LABEL[r.status].color} size={12} />{STATUS_LABEL[r.status].label}
            </span>
            {r.freshness.data_cutoff && (
              <span className="text-[11px] text-ink-3">Data to {r.freshness.data_cutoff} NYC · snapshot, not live</span>
            )}
          </div>
          <Warnings warnings={r.warnings} />
          <AnswerText text={r.answer} onCite={cite} />
          {r.charts.length > 0 && <div className="mt-3 grid gap-3 lg:grid-cols-2">{r.charts.map((c, i) => <AnalystChart key={i} chart={c} />)}</div>}
          <div className="mt-3 flex flex-wrap gap-2">
            {r.evidence.length > 0 && (
              <Button onClick={() => setShowEvidence((v) => !v)}>
                <Icon d="M4 6h16M4 12h16M4 18h10" />{showEvidence ? "Hide evidence" : `View evidence (${r.evidence.length})`}
              </Button>
            )}
            <Button onClick={copy} label="Copy answer with evidence"><Icon d="M8 8h12v12H8zM4 16V4h12" />{copied ? "Copied" : "Copy"}</Button>
            {(r.status === "error" || r.status === "fallback") && <Button onClick={onRetry}>Retry</Button>}
          </div>
          {showEvidence && (
            <div className="mt-3 space-y-2" role="group" aria-label="Evidence">
              {r.evidence.map((ev) => (
                <EvidenceCard key={ev.evidence_id} ev={ev} open={!!openEvidence[ev.evidence_id]}
                  onToggle={() => setOpenEvidence((o) => ({ ...o, [ev.evidence_id]: !o[ev.evidence_id] }))}
                  cardRef={(el) => { refs.current[ev.evidence_id] = el; }} />
              ))}
            </div>
          )}
        </div>
      )}
    </article>
  );
}

// ---------------------------------------------------------------- page
export function Analyst() {
  const status = useApi<AnalystStatus>("/api/v1/ai/status");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const maxChars = status.data?.limits.max_question_chars ?? 500;
  const busy = turns.some((t) => t.state === "loading");

  useEffect(() => () => abortRef.current?.abort(), []);

  const run = async (question: string, replaceId?: string) => {
    const q = question.trim();
    if (!q || busy) return;
    const id = replaceId ?? `${Date.now()}`;
    const history = turns.filter((t) => t.id !== id && t.state === "done" && t.result?.status !== "refused").map((t) => t.question).slice(-3);
    setTurns((ts) => replaceId ? ts.map((t) => (t.id === id ? { id, question: q, state: "loading" } : t)) : [...ts, { id, question: q, state: "loading" }]);
    setSelected(id);
    if (!replaceId) setInput("");
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const result = await postJson<Investigation>("/api/v1/ai/investigations", { question: q, history }, ctrl.signal);
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, state: "done", result } : t)));
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      const error = e instanceof ApiError ? e : new ApiError("Unexpected error", 0);
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, state: "error", error } : t)));
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void run(input);
    }
  };
  const newInvestigation = () => {
    abortRef.current?.abort();
    setTurns([]);
    setSelected(null);
    setInput("");
    inputRef.current?.focus();
  };
  const selectedTurn = turns.find((t) => t.id === selected) ?? turns[turns.length - 1];
  const latest = [...turns].reverse().find((t) => t.result)?.result;

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
      <div className="min-w-0 space-y-4">
        <section aria-label="Analyst status" className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border border-border bg-surface-1 px-4 py-3 text-xs text-ink-2">
          <span className="inline-flex items-center gap-1.5">
            <Icon d="M12 8v4l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z" color="var(--accent-cyan)" />
            {latest?.freshness.data_cutoff
              ? <>Data snapshot to <strong className="text-ink">{latest.freshness.data_cutoff}</strong> NYC (last complete day {latest.freshness.last_complete_date}). Not live monitoring.</>
              : <>Historical snapshot of NYC 311 data, not live monitoring. Freshness is reported with each answer.</>}
          </span>
          {status.status === "success" && (
            <span>
              Provider: <strong className="text-ink">{status.data.provider}</strong>
              {status.data.model ? ` (${status.data.model})` : ""}
              {!status.data.live_llm && status.data.enabled && " · deterministic test provider, not a language model"}
              {!status.data.enabled && " · analyst disabled"}
            </span>
          )}
          {status.status === "error" && <span className="text-[color:var(--status-critical)]">Analyst status unavailable: {status.error.message}</span>}
        </section>

        <section aria-label="Ask a question" className="rounded-xl border border-border bg-surface-1 p-4 sm:p-5">
          <form onSubmit={(e) => { e.preventDefault(); void run(input); }}>
            <label htmlFor="analyst-question" className="text-sm font-semibold text-ink">Question</label>
            <p id="analyst-hint" className="mt-0.5 text-xs text-ink-3">Press Enter to ask, Shift+Enter for a new line. Answers cover residential noise and heat/hot-water requests only.</p>
            <textarea id="analyst-question" ref={inputRef} value={input} rows={3} maxLength={maxChars}
              aria-describedby="analyst-hint analyst-count" onChange={(e) => setInput(e.target.value)} onKeyDown={onKeyDown}
              placeholder="e.g. How did Brooklyn compare with Queens for noise complaints last month?"
              className="mt-2 w-full resize-y rounded-lg border border-border-strong bg-surface-2 p-3 text-sm text-ink placeholder:text-ink-3 focus:outline-2 focus:outline-[color:var(--accent-cyan)]" />
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
              <span id="analyst-count" className="tabular text-[11px] text-ink-3">{input.length}/{maxChars}</span>
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => { setInput(""); inputRef.current?.focus(); }} disabled={!input}>Clear</Button>
                <Button onClick={newInvestigation} disabled={!turns.length && !input}>New investigation</Button>
                <Button type="submit" variant="primary" disabled={!input.trim() || busy}>{busy ? "Investigating…" : "Ask"}</Button>
              </div>
            </div>
          </form>
          {turns.length === 0 && (
            <div className="mt-4">
              <h2 className="text-xs font-semibold text-ink">Suggested questions</h2>
              <ul className="mt-2 flex flex-wrap gap-2" aria-label="Suggested questions">
                {SUGGESTED_QUESTIONS.map((q) => (
                  <li key={q}>
                    <button type="button" onClick={() => void run(q)} disabled={busy}
                      className="rounded-full border border-border px-3 py-1.5 text-left text-xs text-ink-2 hover:border-[color:var(--accent-cyan)]/60 hover:text-ink focus-visible:outline-2 focus-visible:outline-[color:var(--accent-cyan)]">
                      {q}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>

        <section className="space-y-4" aria-label="Answers">
          {turns.map((t) => (
            <TurnView key={t.id} turn={t} selected={selectedTurn?.id === t.id} onSelect={() => setSelected(t.id)}
              onRetry={() => void run(t.question, t.id)} />
          ))}
        </section>
      </div>

      <aside aria-label="Investigation trace" className="min-w-0 xl:sticky xl:top-6 xl:self-start">
        <div className="rounded-xl border border-border bg-surface-1 p-4">
          <h2 className="mb-1 text-sm font-semibold text-ink">Trace</h2>
          {selectedTurn && <p className="mb-3 truncate text-[11px] text-ink-3" title={selectedTurn.question}>For: {selectedTurn.question}</p>}
          <TracePanel key={selectedTurn?.id} result={selectedTurn?.result} />
        </div>
        <div className="mt-4 rounded-xl border border-border bg-surface-1 p-4 text-xs text-ink-2">
          <h2 className="mb-2 text-sm font-semibold text-ink">How answers are checked</h2>
          <ul className="list-disc space-y-1 pl-4">
            <li>Only {status.data?.tools.length ?? 10} allow-listed, read-only MCP tools; no SQL, files or commands.</li>
            <li>Every number must appear in the evidence its sentence cites; otherwise the answer is regenerated or replaced by verified tool summaries.</li>
            <li>Anomaly flags are statistical, not confirmed incidents. The closure-delay model is restricted to research metrics.</li>
          </ul>
        </div>
      </aside>
    </div>
  );
}
