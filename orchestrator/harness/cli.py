"""Command line entry point.

  harness serve                      # start API + control plane
  harness run --repo PATH --task ... # run one agent and stream its trace to the terminal
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from .config import Settings
from .models import AgentSpec, RunSpec


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .api import create_app

    settings = Settings()
    if args.driver:
        settings.sandbox_driver = args.driver
    uvicorn.run(create_app(settings), host=args.host or settings.host, port=args.port or settings.port,
                log_level="info")


async def _run(args: argparse.Namespace) -> int:
    from .orchestrator import Orchestrator

    settings = Settings()
    if args.driver:
        settings.sandbox_driver = args.driver
    settings.warm_pool_size = 0
    settings.max_concurrency = 1
    orch = Orchestrator(settings)
    await orch.start()
    try:
        spec = RunSpec(repo=args.repo, ref=args.ref, task=args.task, test_command=args.test_command,
                       agent=AgentSpec(provider=args.provider, model=args.model))
        run = await orch.submit(spec)
        log = orch.bus.get(run.id)
        assert log
        async for ev in log.follow():
            d = ev.data
            if ev.type == "terminal":
                sys.stdout.write(d["chunk"])
            elif ev.type in {"agent.thinking", "agent.text"}:
                sys.stdout.write(f"\033[2m{d['delta']}\033[0m" if ev.type == "agent.thinking" else d["delta"])
            elif ev.type == "tool.call":
                print(f"\n\033[36m→ {d['name']}\033[0m {json.dumps(d['input'])[:200]}")
            elif ev.type == "command.started":
                print(f"\n\033[33m$ {d['command']}\033[0m")
            elif ev.type in {"phase.started", "sandbox.ready", "test.result", "delivery", "run.completed"}:
                extra = {k: v for k, v in d.items() if k not in {"output", "patch"}}
                print(f"\n\033[35m[{ev.type}]\033[0m {ev.phase.value if ev.phase else ''} {json.dumps(extra)}")
            sys.stdout.flush()
        final = orch.runs[run.id]
        print(f"\nstatus={final.status.value} startup_ms={final.startup.total_ms if final.startup else None}")
        if final.status.value == "succeeded":
            print(f"patch: {settings.artifacts_dir / run.id / 'change.patch'}")
        return 0 if final.status.value == "succeeded" else 1
    finally:
        await orch.shutdown()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="harness")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="start the API and control plane")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--driver", choices=["docker", "local"])
    r = sub.add_parser("run", help="run a single agent and stream its trace")
    r.add_argument("--repo", required=True)
    r.add_argument("--ref")
    r.add_argument("--task", required=True)
    r.add_argument("--test-command")
    r.add_argument("--provider", choices=["anthropic", "scripted"])
    r.add_argument("--model")
    r.add_argument("--driver", choices=["docker", "local"])
    args = p.parse_args()
    if args.cmd == "serve":
        _serve(args)
    else:
        sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
