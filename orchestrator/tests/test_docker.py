import asyncio

import pytest

from harness.models import AgentSpec, RunSpec, RunStatus
from harness.orchestrator import Orchestrator

from .conftest import docker_ready

pytestmark = [pytest.mark.docker,
              pytest.mark.skipif(not docker_ready(), reason="docker daemon or cch-sandbox image not available")]


async def test_docker_sandbox_end_to_end(settings, demo_repo):
    settings.sandbox_driver = "docker"
    settings.warm_pool_size = 2
    orch = Orchestrator(settings)
    await orch.start()
    try:
        r = await orch.submit(RunSpec(repo=demo_repo, task="Fix median()", agent=AgentSpec(provider="scripted")))
        await asyncio.wait_for(orch.wait(r.id), 120)
        run = orch.runs[r.id]
        assert run.status == RunStatus.succeeded, run.error
        assert run.startup.warm
    finally:
        await orch.shutdown()


async def test_docker_sandbox_is_isolated(settings):
    settings.sandbox_driver = "docker"
    orch = Orchestrator(settings)
    await orch.start()
    try:
        sb = await orch.pool.acquire()
        net = await sb.exec("python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 2)\"")
        assert not net.ok  # no network
        assert (await sb.exec("id -u")).stdout.strip() != "0"  # not root
        ro = await sb.exec("touch /etc/x")
        assert not ro.ok  # read-only root filesystem
        await orch.pool.release(sb)
    finally:
        await orch.shutdown()
