"""HTTP + Server-Sent Events API consumed by the React control plane."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings
from .events import dumps
from .models import BatchSpec, RunSpec
from .orchestrator import Orchestrator

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def create_app(settings: Settings | None = None, orchestrator: Orchestrator | None = None) -> FastAPI:
    settings = settings or Settings()
    orch = orchestrator or Orchestrator(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await orch.start()
        yield
        await orch.shutdown()

    app = FastAPI(title="Cloud Coding Harness", version=__version__, lifespan=lifespan)
    app.state.orch = orch
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

    def get_run(run_id: str):
        run = orch.runs.get(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        return run

    @app.get("/api/health")
    async def health():
        return {"ok": True, "version": __version__, "driver": orch.pool.provider.name}

    @app.post("/api/runs", status_code=201)
    async def create_run(spec: RunSpec):
        return (await orch.submit(spec)).public()

    @app.post("/api/runs/batch", status_code=201)
    async def create_batch(spec: BatchSpec):
        base = RunSpec.model_validate(spec.model_dump(exclude={"count"}))
        runs = []
        for i in range(spec.count):
            s = base.model_copy(deep=True)
            s.labels = {**s.labels, "batch_index": str(i)}
            runs.append(await orch.submit(s))
        return {"ids": [r.id for r in runs]}

    @app.get("/api/runs")
    async def list_runs(limit: int = 500):
        return [r.public() for r in orch.list_runs(limit)]

    @app.get("/api/runs/{run_id}")
    async def run_detail(run_id: str):
        return get_run(run_id).public()

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str):
        get_run(run_id)
        return {"cancelled": await orch.cancel(run_id)}

    @app.get("/api/runs/{run_id}/patch")
    async def run_patch(run_id: str):
        get_run(run_id)
        path = settings.artifacts_dir / run_id / "change.patch"
        if not path.exists():
            raise HTTPException(404, "no patch for this run")
        return PlainTextResponse(path.read_text(), headers={
            "Content-Disposition": f'attachment; filename="{run_id}.patch"'})

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request, after: int = 0):
        get_run(run_id)
        log = orch.bus.get(run_id)
        assert log is not None
        last_id = request.headers.get("last-event-id")
        start = int(last_id) if last_id and last_id.isdigit() else after

        async def gen() -> AsyncIterator[str]:
            it = log.follow(start).__aiter__()
            while True:
                try:
                    ev = await asyncio.wait_for(it.__anext__(), timeout=15)
                except StopAsyncIteration:
                    yield "event: end\ndata: {}\n\n"
                    return
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"id: {ev.seq}\ndata: {dumps(ev)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/api/stream")
    async def global_stream():
        q = orch.bus.subscribe_global()

        async def gen() -> AsyncIterator[str]:
            try:
                yield f"data: {json.dumps({'type': 'metrics', 'metrics': orch.metrics()})}\n\n"
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=2)
                        yield f"data: {json.dumps(msg)}\n\n"
                    except TimeoutError:
                        yield f"data: {json.dumps({'type': 'metrics', 'metrics': orch.metrics()})}\n\n"
            finally:
                orch.bus.unsubscribe_global(q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/api/fleet")
    async def fleet(limit: int = 200):
        """Compact snapshot for lightweight pollers (e.g. the Roblox viewer)."""
        m = orch.metrics()
        return {
            "running": m["running"], "queued": m["queue_depth"], "peak": m["peak_running"],
            "succeeded": m["runs"]["succeeded"], "failed": m["runs"]["failed"] + m["runs"]["error"],
            "startup_p50_ms": m["startup_ms"]["p50"],
            "runs": [{"id": r.id, "status": r.status.value, "phase": r.phase.value if r.phase else None,
                      "task": r.spec.task[:80], "turns": r.turns, "tests_passed": r.tests_passed,
                      "files_changed": r.files_changed} for r in orch.list_runs(limit)],
        }

    @app.get("/api/metrics")
    async def metrics():
        return orch.metrics()

    # Serve the built React app when available (docker image / `make build`).
    dist = settings.web_dist or Path(__file__).resolve().parents[2] / "web" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = dist / path
            if path and f.is_file() and dist in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app
