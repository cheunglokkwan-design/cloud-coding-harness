"""Run scheduler and lifecycle manager.

Runs are queued and executed by a fixed set of async workers (``max_concurrency``). Each run:

  1. acquires a sandbox (warm pool first, cold start otherwise)
  2. checks the repository out from the local mirror cache into the sandbox workspace
  3. drives the agent loop
  4. stores the patch and releases (destroys) the sandbox

Everything is asyncio: an agent waiting on the model or on a command in its sandbox costs a
coroutine, not a thread, which is what makes hundreds of concurrent agents cheap on the
orchestrator side. The real limit is sandbox resources on the Docker host.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import statistics
import time
from pathlib import Path
from typing import Any

from .agent.loop import AgentLoop
from .config import Settings
from .events import EventBus
from .llm.base import LLM
from .llm.scripted import ScriptedLLM
from .models import Phase, Run, RunSpec, RunStatus, ScriptedStep, StartupTiming
from .repocache import RepoCache
from .sandbox import WarmPool, make_provider
from .sandbox.base import Sandbox, SandboxProvider

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, settings: Settings, provider: SandboxProvider | None = None) -> None:
        self.settings = settings
        settings.ensure_dirs()
        self.bus = EventBus(settings.artifacts_dir)
        self.cache = RepoCache(settings.mirrors_dir, settings.mirror_refresh_s)
        self.pool = WarmPool(provider or make_provider(settings), settings.warm_pool_size,
                             settings.pool_fill_concurrency)
        self._provision_sem = asyncio.Semaphore(max(1, settings.provision_concurrency))
        self.runs: dict[str, Run] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running = 0
        self.peak_running = 0
        self.started_at = time.time()

    # --- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        await self.pool.start()
        self._workers = [asyncio.create_task(self._worker(i)) for i in range(self.settings.max_concurrency)]
        log.info("orchestrator started: driver=%s concurrency=%d pool=%d", self.pool.provider.name,
                 self.settings.max_concurrency, self.settings.warm_pool_size)

    async def shutdown(self) -> None:
        for t in self._workers:
            t.cancel()
        for t in list(self._tasks.values()):
            t.cancel()
        await asyncio.gather(*self._workers, *self._tasks.values(), return_exceptions=True)
        await self.pool.shutdown()

    # --- public API -----------------------------------------------------------
    async def submit(self, spec: RunSpec) -> Run:
        run = Run(spec=spec)
        self.runs[run.id] = run
        self.bus.open(run.id)
        await self.bus.emit(run.id, "run.queued", spec=spec.model_dump(mode="json"))
        self._publish(run)
        self._queue.put_nowait(run.id)
        return run

    async def cancel(self, run_id: str) -> bool:
        run = self.runs.get(run_id)
        if not run or run.status.terminal:
            return False
        task = self._tasks.get(run_id)
        if task:
            task.cancel()
        else:
            # Still queued: mark it so the worker skips it.
            await self._finish(run, RunStatus.cancelled, error="cancelled before start")
        return True

    def list_runs(self, limit: int = 500) -> list[Run]:
        return sorted(self.runs.values(), key=lambda r: r.created_at, reverse=True)[:limit]

    def metrics(self) -> dict[str, Any]:
        by_status: dict[str, int] = {s.value: 0 for s in RunStatus}
        for r in self.runs.values():
            by_status[r.status.value] += 1
        startups = [r.startup.total_ms for r in self.runs.values() if r.startup]
        durations = [r.finished_at - r.started_at for r in self.runs.values() if r.finished_at and r.started_at]
        return {
            "runs": by_status,
            "running": self._running,
            "peak_running": self.peak_running,
            "queue_depth": self._queue.qsize(),
            "max_concurrency": self.settings.max_concurrency,
            "startup_ms": _summary(startups),
            "run_duration_s": _summary(durations),
            "sandbox": self.pool.stats(),
            "repo_cache": {"hits": self.cache.hits, "misses": self.cache.misses},
            "uptime_s": round(time.time() - self.started_at, 1),
        }

    # --- internals ------------------------------------------------------------
    def _publish(self, run: Run) -> None:
        self.bus.publish_global({"type": "run", "run": run.public()})

    async def _worker(self, idx: int) -> None:
        while True:
            run_id = await self._queue.get()
            run = self.runs.get(run_id)
            if not run or run.status.terminal:
                continue
            task = asyncio.create_task(self._execute(run))
            self._tasks[run_id] = task
            try:
                await task
            except asyncio.CancelledError:
                # Re-raise if the worker itself is being cancelled (shutdown); swallow if only
                # the run was cancelled via the API.
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
            finally:
                self._tasks.pop(run_id, None)

    def _make_llm(self, run: Run, script_from_repo: list[ScriptedStep] | None) -> LLM:
        a = run.spec.agent
        provider = a.provider or self.settings.default_provider
        if provider == "scripted":
            steps = a.script if a.script is not None else script_from_repo
            if steps is None:
                raise ValueError("scripted provider needs agent.script or a .harness/script.json in the repo")
            return ScriptedLLM(steps)
        from .llm.anthropic_llm import AnthropicLLM

        return AnthropicLLM(model=a.model or self.settings.anthropic_model,
                            effort=a.effort or self.settings.anthropic_effort)

    async def _execute(self, run: Run) -> None:
        self._running += 1
        self.peak_running = max(self.peak_running, self._running)
        sb: Sandbox | None = None
        run.started_at = time.time()
        run.status = RunStatus.starting
        run.phase = Phase.provision
        self._publish(run)

        async def emit(type_: str, phase: Phase | None = None, **data: Any) -> None:
            if phase and phase != run.phase and type_ == "phase.started":
                run.phase = phase
                self._publish(run)
            await self.bus.emit(run.id, type_, phase, **data)

        try:
            async with asyncio.timeout(self.settings.run_timeout_s):
                await emit("phase.started", Phase.provision)
                t0 = time.perf_counter()
                async with self._provision_sem:
                    sb = await self.pool.acquire()
                    run.sandbox_id = sb.id
                    checkout = await self.cache.checkout(run.spec.repo, run.spec.ref, sb.host_path,
                                                         f"harness/{run.id}")
                    await self.pool.provider.adopt_workspace(sb)
                    ready = await sb.exec("true", timeout=30)
                if not ready.ok:
                    raise RuntimeError(f"sandbox not ready: {ready.combined()}")
                total = (time.perf_counter() - t0) * 1000
                run.startup = StartupTiming(total_ms=round(total, 1), container_ms=round(sb.container_ms, 1),
                                            checkout_ms=round(checkout.duration_ms, 1), warm=sb.warm,
                                            mirror_hit=checkout.mirror_hit)
                await emit("sandbox.ready", Phase.provision, sandbox_id=sb.id, commit=checkout.commit,
                           driver=self.pool.provider.name, **run.startup.model_dump())
                await emit("phase.completed", Phase.provision, duration_ms=round(total, 1))

                script = await self._repo_script(sb) if (run.spec.agent.provider or
                                                         self.settings.default_provider) == "scripted" else None
                llm = self._make_llm(run, script)
                run.status = RunStatus.running
                self._publish(run)
                loop = AgentLoop(
                    sandbox=sb, llm=llm, task=run.spec.task, emit=emit,
                    artifacts_dir=self.settings.artifacts_dir / run.id,
                    test_command=run.spec.test_command,
                    max_turns=run.spec.agent.max_turns or self.settings.max_agent_turns,
                    max_repair_rounds=self.settings.max_repair_rounds,
                )
                try:
                    result = await loop.run()
                finally:
                    run.turns, run.input_tokens, run.output_tokens = loop.turns, loop.in_tokens, loop.out_tokens
                run.tests_passed = result.tests_passed
                run.summary = result.summary
                run.files_changed = result.files_changed
                ok = result.files_changed > 0 and result.tests_passed is not False
                await self._finish(run, RunStatus.succeeded if ok else RunStatus.failed)
        except asyncio.CancelledError:
            await self._finish(run, RunStatus.cancelled, error="cancelled")
        except TimeoutError:
            await self._finish(run, RunStatus.error, error=f"run exceeded {self.settings.run_timeout_s}s")
        except Exception as e:  # noqa: BLE001 - report any failure on the run itself
            log.exception("run %s failed", run.id)
            await self._finish(run, RunStatus.error, error=f"{type(e).__name__}: {e}")
        finally:
            self._running -= 1
            if sb is not None:
                await self.pool.release(sb)

    async def _repo_script(self, sb: Sandbox) -> list[ScriptedStep] | None:
        try:
            raw = await sb.read_file(".harness/script.json")
        except FileNotFoundError:
            return None
        return [ScriptedStep.model_validate(s) for s in json.loads(raw)]

    async def _finish(self, run: Run, status: RunStatus, error: str | None = None) -> None:
        if run.status.terminal:
            return
        run.status = status
        run.error = error
        run.finished_at = time.time()
        with contextlib.suppress(Exception):
            await self.bus.emit(run.id, "run.completed", None, status=status.value, error=error,
                                tests_passed=run.tests_passed, summary=run.summary,
                                files_changed=run.files_changed)
        log_ = self.bus.get(run.id)
        if log_:
            await log_.close()
        with contextlib.suppress(Exception):
            path: Path = self.settings.artifacts_dir / run.id / "run.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(run.public(), indent=2))
        self._publish(run)

    async def wait(self, run_id: str, timeout: float | None = None) -> Run:
        log_ = self.bus.get(run_id)
        if log_:
            async with asyncio.timeout(timeout):
                async for _ in log_.follow():
                    pass
        return self.runs[run_id]


def _summary(xs: list[float]) -> dict[str, float | int | None]:
    if not xs:
        return {"n": 0, "p50": None, "p95": None, "max": None}
    s = sorted(xs)
    p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]
    return {"n": len(s), "p50": round(statistics.median(s), 1), "p95": round(p95, 1), "max": round(s[-1], 1)}
