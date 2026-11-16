import asyncio

from harness.models import AgentSpec, RunSpec, RunStatus, ScriptedStep
from harness.orchestrator import Orchestrator


def _script(i: int, delay_ms: int = 200):
    return [
        ScriptedStep(delay_ms=delay_ms, tool_calls=[
            {"name": "write_file", "input": {"path": f"out/agent_{i}.txt", "content": f"agent {i}\n"}}]),
        ScriptedStep(delay_ms=delay_ms, tool_calls=[{"name": "finish", "input": {"summary": f"agent {i}"}}]),
    ]


async def test_runs_100_plus_agents_concurrently(settings, tiny_repo):
    settings.max_concurrency = 128
    orch = Orchestrator(settings)
    await orch.start()
    try:
        n = 120
        runs = [await orch.submit(RunSpec(repo=str(tiny_repo), task=f"agent {i}", test_command="true",
                                          agent=AgentSpec(provider="scripted", script=_script(i))))
                for i in range(n)]
        await asyncio.wait_for(asyncio.gather(*(orch.wait(r.id) for r in runs)), 120)
        statuses = [orch.runs[r.id].status for r in runs]
        assert statuses.count(RunStatus.succeeded) == n, statuses
        assert orch.peak_running >= 100
        m = orch.metrics()
        assert m["runs"]["succeeded"] == n and m["startup_ms"]["n"] == n
    finally:
        await orch.shutdown()


async def test_cancel_running_and_queued(settings, tiny_repo):
    settings.max_concurrency = 1
    orch = Orchestrator(settings)
    await orch.start()
    try:
        slow = [ScriptedStep(delay_ms=10_000)]
        a = await orch.submit(RunSpec(repo=str(tiny_repo), task="a", agent=AgentSpec(provider="scripted", script=slow)))
        b = await orch.submit(RunSpec(repo=str(tiny_repo), task="b", agent=AgentSpec(provider="scripted", script=slow)))
        for _ in range(100):
            if orch.runs[a.id].status == RunStatus.running:
                break
            await asyncio.sleep(0.05)
        assert await orch.cancel(b.id)
        assert await orch.cancel(a.id)
        await asyncio.wait_for(orch.wait(a.id), 10)
        assert orch.runs[a.id].status == RunStatus.cancelled
        assert orch.runs[b.id].status == RunStatus.cancelled
    finally:
        await asyncio.wait_for(orch.shutdown(), 10)


async def test_bad_repo_is_reported_as_error(settings, tmp_path):
    orch = Orchestrator(settings)
    await orch.start()
    try:
        r = await orch.submit(RunSpec(repo=str(tmp_path / "nope"), task="x", agent=AgentSpec(provider="scripted",
                                                                                           script=[])))
        await asyncio.wait_for(orch.wait(r.id), 30)
        assert orch.runs[r.id].status == RunStatus.error and orch.runs[r.id].error
    finally:
        await orch.shutdown()
