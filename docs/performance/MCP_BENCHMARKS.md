# MCP performance benchmarks

Status: **measured locally, single machine, under memory pressure; not a production benchmark.**
Raw results: [`mcp_benchmark_results.json`](mcp_benchmark_results.json). Script: [`scripts/mcp_benchmark.py`](../../scripts/mcp_benchmark.py).

## What was measured

Everything uses the real MCP stack: the official `mcp` 2.3 SDK client, a real `python -m civicpulse_mcp` stdio
subprocess, the JSON-RPC protocol and the local DuckDB warehouse. No LLM and no network are involved. The
"investigation" row runs the full orchestrator with the deterministic fake provider over real stdio MCP for
*"Which borough had the most residential noise complaints during October 2026?"* (1 tool call, `compare_boroughs`,
validation passed).

| Phase | What it is |
|---|---|
| spawn_imports | separate process: interpreter start + `import civicpulse_mcp.server` (proxy for spawn cost) |
| session_open | `gateway.session()` enter: spawn + MCP `initialize` handshake |
| discovery | `tools/list` + reading `civicpulse://dataset` |
| first_call | first tool call in a fresh child (cold caches) |
| next_call | later calls in the same session (warm) |
| close | session exit, child shutdown |
| per_request_total | open + discovery + 4 calls + close: the current per-investigation model, without the LLM |
| reused_session | the same discovery + 4 calls on an already-open session (persistent-session model) |
| investigation_fake_provider | complete investigation through the orchestrator (fake provider) |

Memory is measured once, in a separate untimed session (the probe enumerates processes, which would perturb timings).
On Windows the venv `python.exe` is a launcher, so the MCP interpreter is a grandchild; the probe sums all descendants.

## Results (2026-11-05, Windows 11, Python 3.12.0, 16 logical CPUs, 10 iterations)

Free physical memory was 1.9–2.2 GB of 16 GB during the run (other applications were using the rest).

| Phase | n | median ms | p90 ms | p95 ms | min ms | max ms |
|---|---:|---:|---:|---:|---:|---:|
| spawn_imports | 10 | 1602 | 1700 | 1824 | 1301 | 1949 |
| session_open | 10 | 1928 | 2251 | 2281 | 1610 | 2310 |
| discovery | 10 | 91 | 119 | 119 | 80 | 120 |
| first_call | 10 | 383 | 490 | 560 | 343 | 630 |
| next_call | 30 | 174 | 206 | 216 | 104 | 263 |
| close | 10 | 387 | 542 | 542 | 173 | 542 |
| **per_request_total** | 10 | **3239** | 3923 | 4020 | 2798 | 4117 |
| **reused_session** | 10 | **239** | 339 | 346 | 232 | 352 |
| investigation_fake_provider | 5 | 3490 | 3816 | 3820 | 2736 | 3824 |

With n = 10 the p90/p95 values are interpolated between the two largest samples; treat them as indicative only.

| Memory | MB |
|---|---:|
| MCP child resident memory after calls (per-request session) | 204 |
| Persistent child after 11 warm runs | 205 |

| Concurrency (K = 2 simultaneous per-request sessions, 2 rounds) | median ms |
|---|---:|
| wall time for both | 4057 |
| per-session total | 4055 |

The concurrency phase runs only when free memory is at least 2048 MB at that point; it ran in this measurement
(2209 MB free). Higher concurrency was **not** measured because of the memory available on this machine.

### Run-to-run variance

Three runs were made the same day while the script was being corrected. The full fake-provider investigation
measured 2558 ms, 2391 ms and 3490 ms (medians); per-request totals varied similarly. The machine was memory
constrained, so absolute numbers are noisy. The *ratio* between per-request and reused sessions was stable
(about 13–20×). An earlier run reported a 4.2 MB child and inflated totals; that was a bug in the script (it measured
the venv launcher only, and the memory probe ran inside the timed block) and those numbers are not used.

## Where the time goes

* About 2.3 s of each investigation is MCP session lifecycle (spawn + initialize ≈ 1.9 s, close ≈ 0.4 s). Tool
  calls themselves take 0.1–0.6 s.
* Python `-X importtime` shows that importing `civicpulse_mcp.server` takes ~1.15 s, of which ~1.0 s is the `mcp` SDK
  itself (`mcp.client`, `mcp_types`, Starlette/uvicorn for the HTTP transport). Trimming CivicPulse's own imports
  would save at most ~0.1 s.

## Options evaluated

| Option | Latency effect (measured basis) | Isolation | Crash containment | Complexity / risk |
|---|---|---|---|---|
| **Per-request subprocess (current)** | baseline: ~2.3 s lifecycle per investigation | full: fresh child per investigation | a crash affects one investigation | lowest; already tested end to end |
| Persistent single session | saves ~2.3 s (reused_session 239 ms vs 3239 ms) | shared child; tools are read-only and stateless | a crash affects all in-flight investigations; needs respawn | the SDK requires the session to be entered and exited in the same task, so it needs a dedicated owner task and cross-task cancellation |
| Reusable session per worker (pool of N) | same saving, N children (~205 MB each) | one investigation per session at a time | crash affects one worker; respawn needed | worker tasks owned by the app lifespan, job queue, recycle after M uses, health checks |
| Worker pool of processes with HTTP transport | similar saving | per worker | per worker | adds a network listener and token handling |

## Decision

**Keep the per-request subprocess model for Milestone 4.** It is the simplest reliable option: every investigation gets
a fresh, isolated, read-only server; a crash or hang is contained to one request and bounded by timeouts; there is no
long-lived state to recycle; and shutdown is trivial. The measured cost is ~2.3 s per investigation on this machine.

A pooled design would remove most of that cost, but its reliability requirements (bounded pool size tied to
`CIVICPULSE_AI_MAX_CONCURRENT`, exclusive session per investigation, respawn on transport error, recycling after a
fixed number of uses, graceful shutdown in the FastAPI lifespan, cancellation of a job when its request times out)
could not be load-tested here: free memory was ~2 GB and each child uses ~205 MB. Implementing it without that
verification would trade a known latency cost for unverified failure modes.

With a live LLM, each provider call typically takes seconds, so the share of MCP lifecycle in total latency would be
smaller than with the fake provider. That share has **not** been measured (no live LLM run is authorized).

### Before / after

No MCP transport change was made in this milestone, so before = after for the transport. The only Stage F code
change is the benchmark itself. Changes in other stages (claim validation, report storage) add no measurable MCP
calls: investigations still make the same tool calls.

### Planned (not implemented)

* Session pool as described above, behind a setting (default off), with load tests at K = 2, 4 and 8 on a machine
  with enough free memory, crash-injection tests (kill a child mid-call) and shutdown tests.
* Lazy import of the HTTP-transport modules in the stdio server path (bounded by the ~0.1 s CivicPulse import share
  unless the SDK's own imports become lazy).

## Reproduce

```bash
python scripts/mcp_benchmark.py --iterations 10 --concurrency 2   # writes docs/performance/mcp_benchmark_results.json
python -X importtime -c "import civicpulse_mcp.server" 2> importtime.txt
```

The script refuses the concurrency phase when free memory is below `--min-free-mb` (default 2048) and records why.
