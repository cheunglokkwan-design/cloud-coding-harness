import asyncio
import json

import httpx

from harness.api import create_app
from harness.orchestrator import Orchestrator


async def test_api_run_lifecycle_and_sse(settings, demo_repo):
    orch = Orchestrator(settings)
    app = create_app(settings, orch)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            assert (await c.get("/api/health")).json()["ok"]
            r = await c.post("/api/runs", json={"repo": demo_repo, "task": "Fix median()",
                                                "agent": {"provider": "scripted"}})
            assert r.status_code == 201
            run_id = r.json()["id"]
            await asyncio.wait_for(orch.wait(run_id), 60)

            events = []
            async with c.stream("GET", f"/api/runs/{run_id}/events") as s:
                async for line in s.aiter_lines():
                    if line.startswith("data: ") and line != "data: {}":
                        events.append(json.loads(line[6:]))
            types = [e["type"] for e in events]
            assert types[0] == "run.queued" and types[-1] == "run.completed"
            assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

            # Resume from the middle (what EventSource does on reconnect).
            async with c.stream("GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "5"}) as s:
                first = next(json.loads(ln[6:]) for ln in [x async for x in s.aiter_lines()] if ln.startswith("data: {\""))
            assert first["seq"] == 6

            detail = (await c.get(f"/api/runs/{run_id}")).json()
            assert detail["status"] == "succeeded" and detail["tests_passed"] is True
            patch = await c.get(f"/api/runs/{run_id}/patch")
            assert patch.status_code == 200 and "median" in patch.text
            assert (await c.get("/api/metrics")).json()["runs"]["succeeded"] == 1
            fleet = (await c.get("/api/fleet")).json()
            assert fleet["succeeded"] == 1 and fleet["runs"][0]["status"] == "succeeded"
            assert (await c.post("/api/runs/batch", json={"repo": demo_repo, "task": "x", "count": 3,
                                                          "agent": {"provider": "scripted"}})).json()["ids"]
