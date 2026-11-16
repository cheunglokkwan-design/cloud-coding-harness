# Architecture

```mermaid
flowchart LR
  subgraph CP[Control plane]
    UI[React app<br/>fleet · trace · reasoning · terminal · diff]
  end
  subgraph O[Orchestrator process]
    API[FastAPI<br/>REST + SSE]
    SCH[Scheduler<br/>queue + N async workers]
    BUS[Event bus<br/>per-run append-only log]
    LOOP[Agent loop<br/>analyze → generate → test → repair → deliver]
    POOL[Warm pool]
    CACHE[Repo cache<br/>bare mirrors]
  end
  LLM[(Claude API)]
  subgraph H[Docker host]
    S1[sandbox 1]
    S2[sandbox 2]
    SN[sandbox N]
  end
  UI -- POST /api/runs --> API
  API --> SCH --> LOOP
  LOOP -- tool calls --> S1
  LOOP <--> LLM
  LOOP -- events --> BUS -- SSE --> UI
  SCH --> POOL -- Engine API over unix socket --> H
  CACHE -- git clone --shared --> S1
```

## Life of a run

1. **Submit.** `POST /api/runs` creates a `Run`, opens its event log, and puts it on the queue.
   `/api/runs/batch` does this N times.
2. **Schedule.** One of `max_concurrency` async workers picks it up. Every agent is a coroutine.
   An agent waiting on the model or on a command costs no thread and almost no memory, which is
   why 100+ concurrent agents are cheap for the orchestrator itself.
3. **Provision** (`orchestrator.py`, `sandbox/pool.py`, `repocache.py`):
   - Take a running container from the **warm pool**, or create one if the pool is empty. The
     pool refills in the background, at most `pool_fill_concurrency` creations at a time.
   - Make sure a **bare mirror** of the repository exists (`git clone --mirror` on first use,
     `git remote update` when older than `mirror_refresh_s`).
   - `git clone --shared` from the mirror into the sandbox's workspace directory. This writes an
     `objects/info/alternates` pointer and the working tree only. No objects are copied and the
     network isn't touched.
   - Hand ownership of the workspace to the sandbox user, then run a readiness `exec`.
   - At most `provision_concurrency` runs provision at once, so bursts queue instead of thrashing.
4. **Analyze** (`agent/analyze.py`). Deterministic facts go into the first prompt: file inventory,
   languages, manifests, the detected test command, and a README excerpt. Then a **baseline test
   run**, so the model knows what was already failing.
5. **Generate** (`agent/loop.py`, `agent/tools.py`). A tool-use loop with Claude. The tools are
   `list_files`, `read_file`, `write_file`, `edit_file` (exact, unique string replacement),
   `search` (`git grep`), `run_command`, and `finish`. Each executes **inside the sandbox**. Thinking
   and text stream token by token. A coalescer batches them into events every ~150 ms.
6. **Test → repair.** After `finish`, the harness runs the test command itself. If it fails, the
   output goes back to the model in the same conversation, up to `max_repair_rounds` times.
7. **Deliver.** `git add -A`, then a commit inside the sandbox, then `git format-patch`. The patch
   and a summary are written to `artifacts/<run_id>/`. The run succeeds if it changed files and
   the tests did not fail.
8. **Release.** Sandboxes are single-use. The container and its workspace are destroyed.

## Sandboxes

`sandbox/docker.py` talks to the Docker Engine API (`/containers/create`, `/exec`, ...) over the
unix socket with `httpx`. Earlier versions shelled out to the `docker` CLI. At 40+ agents, the CLI
process spawned per command cost more CPU than the commands themselves.

The container profile:

| setting | value | why |
|---|---|---|
| network | `none` | no exfiltration and no dependency on the internet |
| capabilities | all dropped, `no-new-privileges` | removes privilege-escalation paths |
| root filesystem | read-only, `/tmp` tmpfs | the only writable places are the workspace and scratch |
| user | non-root (`1000:1000`) | defense in depth |
| limits | `NanoCpus`, `Memory`, `PidsLimit` | one agent can't starve the host or fork-bomb it |
| mounts | workspace (rw), mirrors (ro, same path as host) | `--shared` alternates resolve inside the container |
| init | `--init` | reaps zombies from agent-run processes |

Every command is wrapped in `timeout -s KILL`, so a runaway process dies even if the client side
goes away. stdout and stderr are demultiplexed from Docker's stream format and forwarded as
`terminal` events while the command runs.

**Why file I/O goes through `exec`.** The workspace is a host directory. If the orchestrator read
and wrote it directly, an agent could create `evil -> /etc/shadow` and ask to write to `evil`, and
the orchestrator would follow the link on the host. Routing reads and writes through the sandbox
keeps symlinks resolved inside the container. Paths are also normalised and anything escaping
`/workspace` is rejected (`safe_relpath`). `git` runs inside the sandbox once the agent has
started.

The `local` driver (`sandbox/local.py`) runs plain subprocesses in a temp directory. It exists for
development and tests and is **not** an isolation boundary.

## Events and streaming

Each run has an append-only event log (`events.py`). Every event has a sequence number, a
timestamp, a phase, a type and data. The log is held in memory for live subscribers and
appended to `artifacts/<run>/events.jsonl`.

- `GET /api/runs/{id}/events` replays from any sequence number and then follows live. The SSE
  `id:` field is the sequence number. A browser `EventSource` reconnects with `Last-Event-ID`
  and resumes without losing events or duplicating them.
- `GET /api/stream` is a global feed: run state changes, plus a metrics snapshot every 2 s.
- Event types include `run.queued`, `phase.started`/`phase.completed`, `sandbox.ready` (with the
  startup breakdown), `analysis`, `agent.turn`, `agent.thinking`/`agent.text` (streamed deltas),
  `agent.message`, `tool.call`/`tool.result`, `command.started`/`terminal`/`command.finished`,
  `test.result`, `diff`, `delivery`, and `run.completed`.

The React app (`web/src/lib/runView.ts`) folds events into per-panel views: a trace timeline,
reasoning per turn, terminal output grouped by command, and the latest diff. It batches updates
with `requestAnimationFrame`, so a chatty run doesn't cause a re-render per event.

## Model integration

`llm/anthropic_llm.py` uses the official Anthropic SDK:

- streaming (`client.beta.messages.stream`) so reasoning reaches the UI as it's generated
- `thinking: {type: "adaptive", display: "summarized"}`, so there is readable reasoning to show
- `output_config.effort` (default `high`)
- strict tool schemas (`strict: true`, `additionalProperties: false`)
- server-side refusal fallbacks (`fallbacks: "default"`), and an explicit `refusal` stop-reason check
- the assistant's full `content` (including thinking blocks) is appended back unchanged each turn

`llm/scripted.py` replays a fixed list of turns. Demos, tests and load benchmarks use it.

## Trade-offs and what's next

- **In-memory state, single process.** This is simple and fast, and it's enough for one host.
  Multi-host would put the queue and run state in Postgres or Redis and run one sandbox agent
  per Docker host.
- **Docker, not microVMs.** Containers share the host kernel. For untrusted code at scale, gVisor
  (`runsc`) or Firecracker is the next step. The provider interface (`SandboxProvider`) is the
  seam for that.
- **Single-use sandboxes.** A container is never reused across runs, which is simpler and safer.
  The warm pool recovers most of the start-up cost.
- **No network in sandboxes.** This is safe and reproducible, but dependencies must be baked into
  the image. A per-repo image cache, built from lockfiles, would be the natural extension.
