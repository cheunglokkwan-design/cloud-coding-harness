# Benchmarks

Everything here was produced by [`scripts/bench.py`](../scripts/bench.py). The raw JSON and Markdown
for each run are in [`docs/benchmarks/`](benchmarks/). Run the same commands on your own hardware
and you should expect different absolute numbers. The relative differences are what the design
is meant to produce.

**Test host:** 4 vCPU Linux VM, Docker 29.9 (overlayfs), Python 3.13. The sandbox image used was
a `python:3.12`-based build of `sandbox-image/`. The build host could not reach the Debian
mirrors, so the image was assembled from a base that already ships git. Image size does not
affect container start time once the image is local.

## 1. Sandbox startup: cold vs cached vs warm

```bash
python scripts/bench.py startup --driver docker --n 20 --repo https://github.com/pallets/flask
```

**Time-to-ready** is measured from "the orchestrator asks for a sandbox" to "the repository is
checked out in a running container and a first command (`git rev-parse HEAD`) has returned".
The script does 20 sequential runs per mode:

| mode | what happens | p50 | p95 |
|---|---|---|---|
| **cold** | new container + full `git clone` from GitHub | 2753 ms | 2976 ms |
| **cached** | new container + `git clone --shared` from the local mirror | 359 ms | 392 ms |
| **warm** | pre-started container from the warm pool + mirror | **151 ms** | 227 ms |

The breakdown at p50 is as follows. Container create+start costs about 140 ms, and the warm pool
removes it entirely. Checkout costs 2.5 s from the network, 125 ms from the mirror, and 93 ms
when the host isn't also starting a container. The cold number depends on network distance to
GitHub, and larger repositories widen the gap.

## 2. Concurrent agents

```bash
python scripts/bench.py concurrency --driver docker --agents 120 --step-delay-ms 3000 --pool 128
```

N scripted agents are submitted at the same instant. Each one runs the full harness loop:
provision, analyze (`git ls-files`, reading manifests), three model turns that each wait 3 s to
stand in for model latency, a write, a shell command, the test command, diffing, and delivery
(commit + `format-patch`). The benchmark measures the harness and sandboxes, not the model.

| scenario | succeeded | peak concurrent | startup p50 / p95 | run p50 | wall clock |
|---|---|---|---|---|---|
| 30 agents, pool 32 | 30/30 | 30 | 865 / 1822 ms | 18.2 s | 18.8 s |
| 120 agents, pool 128 | 120/120 | **120** | 5.6 / 12.7 s | 53.1 s | 58.5 s |
| 120 agents, pool 16 | 120/120 | 120 | 8.3 / 21.3 s | 52.7 s | 59.2 s |

### How to read this

- **Concurrency holds.** All 120 agents were in flight at the same time and all succeeded. The
  orchestrator itself runs about 120 coroutines and is nowhere near its limit.
- **Startup under a burst is CPU-bound.** A 4-vCPU host absorbing 120 simultaneous starts runs
  roughly 120 × (checkout + chown + first exec) plus 120 agents' worth of `runc exec` calls at
  once. Startup p50 rises from 151 ms (one at a time) to 865 ms (30 at once) and to 5.6 s
  (120 at once). The pool sized to the burst (128) beats a small pool (16), because a small
  pool turns most starts into container creations. `provision_concurrency` (default 16) caps
  how many sandboxes are provisioned at once, so a burst queues instead of thrashing. That wait
  is included in the numbers above.
- **What "under 5 s" means.** Single-agent time-to-ready is 151 ms warm and 359 ms cached. That
  is comfortably under 5 s, and so is a 30-agent burst on 4 vCPUs. A 120-agent burst on the
  same 4 vCPUs is not (p50 5.6 s). It needs more cores or more hosts, roughly 1 vCPU per 8–10
  agents starting simultaneously by these numbers.
- **Run duration is dominated by `docker exec`.** An ideal run is about 9 s of simulated model
  time plus a few hundred ms of commands. At 120-way concurrency each run took about 53 s
  because every command is a `runc exec` competing for 4 cores. With a real model taking 10–60 s
  per turn, this overhead becomes a much smaller share.

## Reproducing

```bash
make setup && make image
make bench-startup        # writes docs/benchmarks/startup-flask.{json,md}
make bench-concurrency    # writes docs/benchmarks/concurrency-120-prewarmed.{json,md}
python scripts/bench.py concurrency --agents 30 --pool 32 --out /tmp/c30.json
```

Use `--driver local` to measure orchestrator overhead without Docker. That driver is not isolated.
