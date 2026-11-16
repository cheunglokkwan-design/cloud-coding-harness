#!/usr/bin/env python3
"""Reproducible benchmarks for sandbox startup and concurrent agent throughput.

Usage (from the repo root, with the orchestrator package installed):

  python scripts/bench.py startup     --driver docker --n 20 --repo https://github.com/pallets/flask
  python scripts/bench.py concurrency --driver docker --agents 120 --step-delay-ms 2000

``startup`` measures time-to-ready (sandbox running + repository checked out + first command
returned) for three configurations, one run at a time:

  cold      new container, no repository cache: every run does a full `git clone`
  cached    new container, repository served from the local mirror cache
  warm      pre-started container from the warm pool + mirror cache (the default setup)

``concurrency`` submits N scripted agents at once (each writes a file, runs a command, and
waits ``--step-delay-ms`` per turn to stand in for model latency) and reports how many ran
concurrently, startup percentiles under load, and wall-clock time. It measures the harness,
not the model.

Results are printed as Markdown and written to ``--out`` (JSON) so they can be committed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))

from harness.config import Settings  # noqa: E402
from harness.models import AgentSpec, RunSpec, ScriptedStep  # noqa: E402
from harness.orchestrator import Orchestrator  # noqa: E402
from harness.repocache import RepoCache  # noqa: E402
from harness.sandbox import WarmPool, make_provider  # noqa: E402

DEMO = str(Path(__file__).resolve().parents[1] / "examples" / "demo-calc")


def _display(repo: str) -> str:
    root = str(Path(__file__).resolve().parents[1]) + "/"
    return repo.replace(root, "")


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def summarize(xs: list[float]) -> dict:
    return {"n": len(xs), "p50": round(statistics.median(xs), 1), "p95": round(pct(xs, 0.95), 1),
            "min": round(min(xs), 1), "max": round(max(xs), 1)}


def host_info(driver: str) -> dict:
    return {"platform": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count(),
            "driver": driver}


async def bench_startup(args) -> dict:
    data = Path(tempfile.mkdtemp(prefix="cch-bench-"))
    s = Settings(data_dir=data, sandbox_driver=args.driver, warm_pool_size=0)
    s.ensure_dirs()
    provider = make_provider(s)
    pool = WarmPool(provider, size=0)
    await pool.start()
    results: dict[str, dict] = {}
    shared_cache = RepoCache(s.mirrors_dir, refresh_s=s.mirror_refresh_s)
    try:
        for mode in ("cold", "cached", "warm"):
            times, parts = [], {"container": [], "checkout": []}
            if mode == "warm":
                pool.size = 1  # keep one pre-started sandbox ready between runs, like production
                pool._refill()
            for i in range(args.n):
                if mode == "warm":
                    for _ in range(400):  # let the pool refill between sequential runs
                        if pool._ready.qsize():
                            break
                        await asyncio.sleep(0.01)
                if mode == "cold":
                    cache = RepoCache(data / f"mirrors-{mode}-{i}")
                    cache.mirrors_dir.mkdir(parents=True, exist_ok=True)
                else:
                    cache = shared_cache
                    if i == 0:
                        await cache.ensure_mirror(args.repo)  # prime once; not timed
                t0 = time.perf_counter()
                sb = await pool.acquire()
                co = await cache.checkout(args.repo, None, sb.host_path, "bench")
                await provider.adopt_workspace(sb)
                r = await sb.exec("git rev-parse HEAD", timeout=30)
                total = (time.perf_counter() - t0) * 1000
                assert r.ok, r.combined()
                times.append(total)
                parts["container"].append(0.0 if sb.warm else sb.container_ms)
                parts["checkout"].append(co.duration_ms)
                await pool.release(sb)
                if mode == "cold":
                    shutil.rmtree(cache.mirrors_dir, ignore_errors=True)
            results[mode] = {"total_ms": summarize(times), "container_ms": summarize(parts["container"]),
                             "checkout_ms": summarize(parts["checkout"])}
            print(f"  {mode:7s} p50={results[mode]['total_ms']['p50']:8.1f} ms  p95={results[mode]['total_ms']['p95']:8.1f} ms",
                  file=sys.stderr)
    finally:
        await pool.shutdown()
        shutil.rmtree(data, ignore_errors=True)
    return {"benchmark": "startup", "repo": _display(args.repo), "n": args.n, "host": host_info(args.driver), "results": results}


def scripted(i: int, delay_ms: int) -> list[ScriptedStep]:
    return [
        ScriptedStep(delay_ms=delay_ms, thinking=f"agent {i}: inspect", tool_calls=[
            {"name": "list_files", "input": {"path": "."}}]),
        ScriptedStep(delay_ms=delay_ms, tool_calls=[
            {"name": "write_file", "input": {"path": f"bench/agent_{i}.txt", "content": f"agent {i}\n"}},
            {"name": "run_command", "input": {"command": "git status --short | wc -l", "timeout_s": 60}}]),
        ScriptedStep(delay_ms=delay_ms, tool_calls=[{"name": "finish", "input": {"summary": f"agent {i} done"}}]),
    ]


async def bench_concurrency(args) -> dict:
    data = Path(tempfile.mkdtemp(prefix="cch-bench-"))
    s = Settings(data_dir=data, sandbox_driver=args.driver, warm_pool_size=args.pool,
                 max_concurrency=max(args.agents, 1), max_repair_rounds=0)
    orch = Orchestrator(s)
    await orch.start()
    samples: list[int] = []
    stop = asyncio.Event()

    async def sampler():
        while not stop.is_set():
            samples.append(orch._running)
            await asyncio.sleep(0.1)

    try:
        await orch.cache.ensure_mirror(args.repo)
        smp = asyncio.create_task(sampler())
        t0 = time.perf_counter()
        runs = [await orch.submit(RunSpec(repo=args.repo, task=f"bench {i}", test_command="true",
                                          agent=AgentSpec(provider="scripted", script=scripted(i, args.step_delay_ms))))
                for i in range(args.agents)]
        await asyncio.wait_for(asyncio.gather(*(orch.wait(r.id) for r in runs)), args.timeout)
        wall = time.perf_counter() - t0
        stop.set()
        await smp
        final = [orch.runs[r.id] for r in runs]
        ok = sum(r.status.value == "succeeded" for r in final)
        startups = [r.startup.total_ms for r in final if r.startup]
        durations = [r.finished_at - r.started_at for r in final if r.finished_at and r.started_at]
        res = {
            "agents": args.agents, "succeeded": ok, "peak_concurrent": orch.peak_running,
            "wall_clock_s": round(wall, 2), "step_delay_ms": args.step_delay_ms, "warm_pool": args.pool,
            "startup_ms": summarize(startups), "run_duration_s": summarize(durations),
            "warm_hits": orch.pool.warm_hits, "cold_starts": orch.pool.cold_starts,
        }
        print(f"  {ok}/{args.agents} succeeded, peak {orch.peak_running} concurrent, wall {wall:.1f}s, "
              f"startup p50 {res['startup_ms']['p50']} ms p95 {res['startup_ms']['p95']} ms", file=sys.stderr)
    finally:
        stop.set()
        await orch.shutdown()
        shutil.rmtree(data, ignore_errors=True)
    return {"benchmark": "concurrency", "repo": _display(args.repo), "host": host_info(args.driver), "results": res}


def to_markdown(r: dict) -> str:
    h = r["host"]
    head = f"Host: {h['cpus']} vCPU, {h['platform']}, Python {h['python']}, driver `{h['driver']}`. Repo: `{r['repo']}`.\n\n"
    if r["benchmark"] == "startup":
        lines = ["| mode | p50 total | p95 total | p50 container | p50 checkout |", "|---|---|---|---|---|"]
        for mode, v in r["results"].items():
            lines.append(f"| {mode} | {v['total_ms']['p50']} ms | {v['total_ms']['p95']} ms | "
                         f"{v['container_ms']['p50']} ms | {v['checkout_ms']['p50']} ms |")
        return head + f"n = {r['n']} sequential runs per mode.\n\n" + "\n".join(lines) + "\n"
    v = r["results"]
    return head + "\n".join([
        "| metric | value |", "|---|---|",
        f"| agents submitted at once | {v['agents']} |",
        f"| succeeded | {v['succeeded']} |",
        f"| peak concurrent agents | {v['peak_concurrent']} |",
        f"| simulated model latency per turn | {v['step_delay_ms']} ms |",
        f"| startup p50 / p95 under load | {v['startup_ms']['p50']} / {v['startup_ms']['p95']} ms |",
        f"| run duration p50 / p95 | {v['run_duration_s']['p50']} / {v['run_duration_s']['p95']} s |",
        f"| wall clock for the batch | {v['wall_clock_s']} s |",
        f"| warm-pool hits / cold starts | {v['warm_hits']} / {v['cold_starts']} |",
    ]) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("startup")
    a.add_argument("--n", type=int, default=20)
    b = sub.add_parser("concurrency")
    b.add_argument("--agents", type=int, default=120)
    b.add_argument("--step-delay-ms", type=int, default=2000)
    b.add_argument("--pool", type=int, default=16)
    b.add_argument("--timeout", type=float, default=900)
    for x in (a, b):
        x.add_argument("--driver", choices=["docker", "local"], default="docker")
        x.add_argument("--repo", default=DEMO)
        x.add_argument("--out", type=Path)
    args = p.parse_args()
    res = asyncio.run(bench_startup(args) if args.cmd == "startup" else bench_concurrency(args))
    md = to_markdown(res)
    print(md)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, indent=2))
        args.out.with_suffix(".md").write_text(md)


if __name__ == "__main__":
    main()
