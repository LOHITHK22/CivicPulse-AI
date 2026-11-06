"""MCP performance benchmark: real stdio subprocesses, real protocol, real warehouse. No network, no LLM.

Measures, per iteration (sequential by default):
  spawn_imports_ms      separate process: interpreter start + importing the MCP server module (spawn cost proxy)
  session_open_ms       gateway.session() enter: spawn + MCP initialize handshake
  discovery_ms          tools/list + reading the civicpulse://dataset resource
  first_call_ms         first tool call (compare_boroughs) in a fresh session (cold caches in the child)
  next_call_ms          each later tool call in the same session (warm)
  close_ms              session exit (child shutdown)
  per_request_total_ms  open + discovery + 4 calls + close: the current per-investigation model, without the LLM
  reused_session_ms     the same discovery + 4 calls on an already-open session (persistent-session model)
  investigation_ms      full orchestrator run with the deterministic fake provider over real stdio MCP
  child_rss_mb          working set of the MCP child after warm calls

Concurrency (K simultaneous per-request sessions) runs only when free physical memory is above
--min-free-mb; otherwise it is recorded as skipped with the measured free memory.

Usage: python scripts/mcp_benchmark.py [--iterations 8] [--concurrency 2]
       [--out docs/performance/mcp_benchmark_results.json]
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import datetime as dt
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CALLS: list[tuple[str, dict[str, Any]]] = [
    (
        "compare_boroughs",
        {
            "start_date": "2026-10-01",
            "end_date": "2026-10-31",
            "boroughs": ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"],
            "category": "noise_residential",
        },
    ),
    ("get_service_request_trends", {"start_date": "2026-10-01", "end_date": "2026-10-31", "granularity": "day"}),
    ("get_category_breakdown", {"start_date": "2026-10-01", "end_date": "2026-10-31"}),
    ("get_dataset_quality", {}),
]
QUESTION = "Which borough had the most residential noise complaints during October 2026?"


# ---------------------------------------------------------------------------------------------- system info
def free_memory_mb() -> float | None:
    if sys.platform == "win32":

        class MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        st = MemStatus()
        st.dwLength = ctypes.sizeof(MemStatus)
        kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009 - windll exists only on Windows
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return float(st.ullAvailPhys) / 2**20
        return None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    except OSError:
        return None
    return None


def _process_table() -> list[tuple[int, int, int]]:
    """(pid, parent pid, resident bytes) for every process."""
    rows: list[tuple[int, int, int]] = []
    if sys.platform == "win32":
        cmd = (
            "Get-CimInstance Win32_Process | ForEach-Object "
            '{ "{0},{1},{2}" -f $_.ProcessId, $_.ParentProcessId, $_.WorkingSetSize }'
        )
        argv = ["powershell", "-NoProfile", "-Command", cmd]  # fixed built-in command, no external input
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
        except (OSError, subprocess.TimeoutExpired):
            return rows
        for line in proc.stdout.splitlines():
            parts = line.strip().split(",")
            if len(parts) == 3 and all(x.isdigit() for x in parts):
                rows.append((int(parts[0]), int(parts[1]), int(parts[2])))
        return rows
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            ppid = int((d / "stat").read_text().rsplit(")", 1)[1].split()[1])
            rss = next(
                int(line.split()[1]) * 1024
                for line in (d / "status").read_text().splitlines()
                if line.startswith("VmRSS:")
            )
            rows.append((int(d.name), ppid, rss))
        except (OSError, ValueError, IndexError, StopIteration):
            continue
    return rows


def child_rss_mb(parent_pid: int) -> float | None:
    """Resident memory of all descendants of this process (on Windows the venv python.exe is a launcher, so the
    MCP interpreter is a grandchild; summing descendants counts both)."""
    rows = _process_table()
    children: dict[int, list[tuple[int, int]]] = {}
    for pid, ppid, rss in rows:
        children.setdefault(ppid, []).append((pid, rss))
    total, stack, seen = 0, [parent_pid], set()
    while stack:
        for pid, rss in children.get(stack.pop(), []):
            if pid not in seen and pid != parent_pid:
                seen.add(pid)
                total += rss
                stack.append(pid)
    return round(total / 2**20, 1) if total else None


def summarize(values: list[float]) -> dict[str, float | int]:
    v = sorted(values)
    n = len(v)

    def pct(p: float) -> float:
        if n == 1:
            return v[0]
        k = (n - 1) * p
        lo, hi = int(k), min(int(k) + 1, n - 1)
        return v[lo] + (v[hi] - v[lo]) * (k - lo)

    return {
        "n": n,
        "median": round(statistics.median(v), 1),
        "p90": round(pct(0.90), 1),
        "p95": round(pct(0.95), 1),
        "min": round(v[0], 1),
        "max": round(v[-1], 1),
    }


# ---------------------------------------------------------------------------------------------- measurements
def spawn_imports_ms() -> float:
    t0 = time.perf_counter()
    subprocess.run([sys.executable, "-c", "import civicpulse_mcp.server"], check=True, capture_output=True)
    return (time.perf_counter() - t0) * 1000


async def per_request(gateway: Any, probe: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"next_calls": []}
    probe_ms = 0.0
    t_all = time.perf_counter()
    t0 = time.perf_counter()
    cm = gateway.session()
    session = await cm.__aenter__()
    try:
        out["session_open"] = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        await session.list_tools()
        await session.read_json_resource("civicpulse://dataset")
        out["discovery"] = (time.perf_counter() - t0) * 1000
        for i, (tool, args) in enumerate(CALLS):
            res = await session.call(tool, args)
            if not res.ok:
                raise RuntimeError(f"{tool} failed: {res.error}")
            (out.__setitem__("first_call", res.duration_ms) if i == 0 else out["next_calls"].append(res.duration_ms))
        if probe:  # memory probe runs only in a separate, untimed session (it spawns a process-table query)
            t_probe = time.perf_counter()
            out["child_rss_mb"] = child_rss_mb(os.getpid())
            probe_ms = (time.perf_counter() - t_probe) * 1000
    finally:
        t0 = time.perf_counter()
        await cm.__aexit__(None, None, None)
        out["close"] = (time.perf_counter() - t0) * 1000
    out["total"] = (time.perf_counter() - t_all) * 1000 - probe_ms
    return out


async def reused(session: Any) -> float:
    t0 = time.perf_counter()
    await session.list_tools()
    await session.read_json_resource("civicpulse://dataset")
    for tool, args in CALLS:
        res = await session.call(tool, args)
        if not res.ok:
            raise RuntimeError(f"{tool} failed: {res.error}")
    return (time.perf_counter() - t0) * 1000


async def investigation(data_dir: Path) -> tuple[float, dict[str, Any]]:
    from civicpulse_agent.budget import Prices, UsageGovernor
    from civicpulse_agent.config import AgentSettings
    from civicpulse_agent.orchestrator import Orchestrator
    from civicpulse_agent.providers import FakeProvider

    s = AgentSettings(_env_file=None, data_dir=data_dir, llm_provider="fake")  # type: ignore[call-arg]
    orch = Orchestrator(s, FakeProvider(), UsageGovernor(1000, 10_000, 0, Prices(0, 0)), today=dt.date(2026, 11, 4))
    t0 = time.perf_counter()
    r = await orch.investigate(QUESTION, "bench")
    ms = (time.perf_counter() - t0) * 1000
    tools = [t.name for t in r.trace if t.kind == "tool"]
    return ms, {"status": r.status, "tools": tools, "validation_ok": r.validation.get("ok")}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    from civicpulse_mcp.client import McpGateway, McpSettings
    from civicpulse_shared.config import get_settings

    data_dir = Path(args.data_dir) if args.data_dir else get_settings().data_dir
    gateway = McpGateway(McpSettings(transport="stdio", data_dir=data_dir, call_timeout=60))
    res: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "free_memory_mb_at_start": round(free_memory_mb() or 0),
        },
        "iterations": args.iterations,
        "data_dir_name": data_dir.name,
        "calls": [c[0] for c in CALLS],
    }
    spawn = [spawn_imports_ms() for _ in range(args.iterations)]
    print(f"spawn+imports median {statistics.median(spawn):.0f} ms", flush=True)

    rows = []
    for i in range(args.iterations):
        rows.append(await per_request(gateway))
        print(f"per-request {i + 1}/{args.iterations}: {rows[-1]['total']:.0f} ms", flush=True)

    reuse: list[float] = []
    async with gateway.session() as session:
        await reused(session)  # warm-up, not counted (equivalent to the first request after start)
        for _ in range(args.iterations):
            reuse.append(await reused(session))
        rss_persistent = child_rss_mb(os.getpid())
    print(f"reused-session median {statistics.median(reuse):.0f} ms", flush=True)

    inv: list[float] = []
    inv_detail: dict[str, Any] = {}
    for _ in range(max(3, args.iterations // 2)):
        ms, inv_detail = await investigation(data_dir)
        inv.append(ms)
    print(f"investigation median {statistics.median(inv):.0f} ms", flush=True)

    res["results_ms"] = {
        "spawn_imports": summarize(spawn),
        "session_open": summarize([r["session_open"] for r in rows]),
        "discovery": summarize([r["discovery"] for r in rows]),
        "first_call": summarize([r["first_call"] for r in rows]),
        "next_call": summarize([x for r in rows for x in r["next_calls"]]),
        "close": summarize([r["close"] for r in rows]),
        "per_request_total": summarize([r["total"] for r in rows]),
        "reused_session": summarize(reuse),
        "investigation_fake_provider": summarize(inv),
    }
    probe_run = await per_request(gateway, probe=True)
    rss = [probe_run["child_rss_mb"]] if probe_run.get("child_rss_mb") else []
    res["memory_mb"] = {
        "per_request_child_rss_after_calls": round(statistics.median(rss), 1) if rss else None,
        "persistent_child_rss_after_warm_runs": rss_persistent,
    }
    res["investigation"] = {"question": QUESTION, **inv_detail}

    free = free_memory_mb() or 0
    if args.concurrency > 1 and free >= args.min_free_mb:

        async def timed() -> float:
            r = await per_request(gateway)
            return float(r["total"])

        walls, each = [], []
        for _ in range(max(2, args.iterations // 4)):
            t0 = time.perf_counter()
            totals = await asyncio.gather(*(timed() for _ in range(args.concurrency)))
            walls.append((time.perf_counter() - t0) * 1000)
            each += totals
        res["concurrency"] = {
            "k": args.concurrency,
            "wall_ms": summarize(walls),
            "per_session_total_ms": summarize(each),
            "free_memory_mb_before": round(free),
        }
    else:
        res["concurrency"] = {
            "k": args.concurrency,
            "skipped": True,
            "reason": f"free physical memory {free:.0f} MB below the {args.min_free_mb} MB threshold"
            if args.concurrency > 1
            else "concurrency disabled",
        }
    res["machine"]["free_memory_mb_at_end"] = round(free_memory_mb() or 0)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=8)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--min-free-mb", type=int, default=2048)
    ap.add_argument("--data-dir", default="")
    ap.add_argument("--out", default=str(ROOT / "docs" / "performance" / "mcp_benchmark_results.json"))
    args = ap.parse_args()
    res = asyncio.run(run(args))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(res["results_ms"], indent=2))
    print(json.dumps({"memory_mb": res["memory_mb"], "concurrency": res["concurrency"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
