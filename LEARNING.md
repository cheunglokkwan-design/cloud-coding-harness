# Learning guide: how this project works, from zero

This guide is for you if you want to understand this project well enough to explain it,
change it, and defend it in an interview. It assumes you know some Python and JavaScript and
not much else. Read it top to bottom once. Then keep the code open next to it.

**Contents**

1. [The big idea in one paragraph](#1-the-big-idea-in-one-paragraph)
2. [Concepts you need](#2-concepts-you-need)
3. [Run it yourself (15 minutes)](#3-run-it-yourself-15-minutes)
4. [Code tour, file by file](#4-code-tour-file-by-file)
5. [Follow one run through the system](#5-follow-one-run-through-the-system)
6. [The performance story: warm pools and git caching](#6-the-performance-story)
7. [Security: why the sandbox is shaped the way it is](#7-security)
8. [Exercises to make it yours](#8-exercises-to-make-it-yours)
9. [Talking about it in interviews](#9-talking-about-it-in-interviews)
10. [Glossary](#10-glossary)

---

## 1. The big idea in one paragraph

An **AI coding agent** is a program that gives an LLM (Claude) a task like "fix this bug" and a
set of **tools** ("read a file", "edit a file", "run the tests"). It then loops: the model picks
a tool, the program runs it, the result goes back to the model, and so on until the job is done.
Running that agent on your laptop is easy. Running **a hundred of them at once, safely, quickly,
and in a way a human can watch**, is the hard part. That's what a **harness** is. This project
starts an isolated **sandbox** (a locked-down container) for each agent, gets the code into it
fast, runs the agent loop, checks the result with tests, and streams everything to a web
dashboard.

## 2. Concepts you need

### 2.1 LLM agents and tool use

Claude doesn't run code. It only produces text. With **tool use**, you describe functions to the
model as JSON schemas, for example:

```json
{"name": "read_file", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}
```

The model can then answer with a structured request, like `tool_use: read_file {"path": "calc/stats.py"}`.
*Your code* runs it and sends back a `tool_result`. Repeat that and you have an **agent loop**:

```
messages = [task]
loop:
    reply = model(messages, tools)
    if reply has no tool calls: stop
    results = [run(tool) for tool in reply.tool_calls]
    messages += [reply, results]
```

Look for that shape in `orchestrator/harness/agent/loop.py` → `_tool_loop`. Everything else in
the project exists to make this loop safe, fast, observable and parallel.

Two Claude API features matter here:

- **Adaptive thinking.** The model can reason before answering. We ask for a *summarized* view
  of that reasoning (`display: "summarized"`) so the dashboard can show why the agent did what
  it did.
- **Streaming.** Instead of waiting for the whole reply, we receive it token by token. That's how
  the Reasoning tab updates live.

### 2.2 Containers and sandboxes

A **container** is a normal Linux process that the kernel *fences in*. It gets its own view of
the filesystem, its own process list and its own network, plus limits on CPU and memory. Docker
is the tool that creates them. An **image** is the read-only template, a snapshot of a filesystem
with Python, git, pytest and so on installed. A container is a running instance of an image.

A **sandbox** is a container configured so that the code inside can't cause harm. The agent will
run commands that an LLM wrote, on code from the internet, so we assume they might be dangerous.
In this project's sandboxes:

- there's no network (it can't download malware or leak your code)
- there are no Linux "capabilities" and no privilege escalation (it can't become root)
- the root filesystem is read-only (the only writable places are the repo and `/tmp`)
- CPU, memory and process limits are set (it can't freeze the machine or fork-bomb it)

Read `sandbox/docker.py` → `_config()`. Each of those lines is one of the bullets above.

### 2.3 Why concurrency is cheap here: async I/O

An agent spends almost all of its time **waiting**: on the model (seconds), on tests, or on
Docker. With one thread per agent, 100 agents means 100 threads, each mostly asleep. Python's
**asyncio** runs all of them in a single thread instead. When an agent `await`s something, the
event loop switches to another agent that's ready to continue. 120 agents cost 120 small
coroutine objects. Look at `orchestrator.py` → `_worker`, `_execute`.

The real limit is the machine running the containers. See [§6](#6-the-performance-story).

### 2.4 Git internals you need

- A **bare mirror** (`git clone --mirror`) is a copy of a repository's database with no
  working files. We keep one per repository in `data/mirrors/`. It's the **cache**.
- `git clone --shared <mirror> <dir>` creates a new repo whose `.git/objects/info/alternates`
  file says "borrow objects from that mirror". Nothing gets copied and nothing is downloaded. Git
  only writes out the working files. That's why a checkout takes about 100 ms instead of seconds.
- A **patch** (`git format-patch`) is a text file that describes a commit. Anyone can apply it
  with `git am`. That's what the harness delivers.

### 2.5 Streaming to the browser: Server-Sent Events

The browser needs updates **pushed** to it. **SSE** is the simplest way to do that: the browser
opens `new EventSource(url)`, and the server keeps the HTTP response open and writes lines
like this:

```
id: 42
data: {"type": "terminal", "data": {"chunk": "4 passed"}}
```

If the connection drops, the browser reconnects automatically and sends `Last-Event-ID: 42`.
Our server then replays from event 43, so nothing is lost. SSE is one-way (server → browser),
which is exactly what we need. Commands like "start a run" use normal POST requests. (WebSockets
are two-way, but they need more machinery than this project requires.)

### 2.6 React in two minutes

React renders UI from **state**. When the state changes, React re-renders. Our state is "all
events received so far for this run". A **reducer** (`web/src/lib/runView.ts`) turns that list
into the shapes each tab needs: the trace timeline, reasoning per turn, terminal output per
command, and the latest diff. A **hook** (`useRunView` in `web/src/lib/api.ts`) owns the
EventSource and feeds new events into the reducer. Events can arrive hundreds per second, so the
hook collects them and applies them once per animation frame (`requestAnimationFrame`).

## 3. Run it yourself (15 minutes)

You need Docker, Python 3.11+ and Node 20+.

```bash
git clone https://github.com/cheunglokkwan-design/cloud-coding-harness
cd cloud-coding-harness
make setup          # Python venv + npm install
make image          # build the sandbox image
make demo           # watch one scripted agent fix a bug, in your terminal
```

Things to notice in the `make demo` output:

1. `[sandbox.ready] ... total_ms` shows how long the sandbox took to be ready.
2. The baseline tests **fail** (2 failed). That's the bug.
3. The agent reads files, edits `calc/stats.py`, and re-runs the tests.
4. `[test.result] passed: true`, then `[delivery]` with a patch path. Open that file.

Now try the dashboard:

```bash
cd web && npm run build && cd ..
make api            # open http://localhost:8080
```

Launch the demo once, then click the run and look at every tab. Next, set **Agents** to 50 and
watch the Fleet view fill up and change colour. Then run the tests: `make test`.

To use the real model, set `export ANTHROPIC_API_KEY=...`, choose **Claude** in the form, and
give it a task of your own.

## 4. Code tour, file by file

Read them in this order. Each one is short.

| # | File | What to look for |
|---|---|---|
| 1 | `orchestrator/harness/models.py` | The data shapes: `RunSpec` (input), `Run` (state), `Event`. |
| 2 | `orchestrator/harness/config.py` | Every knob, all settable with `HARNESS_*` env vars. |
| 3 | `orchestrator/harness/sandbox/base.py` | The `Sandbox` interface: `exec`, `read_file`, `write_file`. `safe_relpath` blocks `../` escapes. `run_process` streams output while it runs. |
| 4 | `orchestrator/harness/sandbox/docker.py` | Talks to Docker's HTTP API over a unix socket. `_config()` is the security profile. `exec()` demultiplexes Docker's stdout/stderr stream. |
| 5 | `orchestrator/harness/sandbox/pool.py` | The warm pool: a queue of ready containers, refilled in the background. |
| 6 | `orchestrator/harness/repocache.py` | Mirrors and `clone --shared`. Plain folders are snapshotted into git. |
| 7 | `orchestrator/harness/agent/tools.py` | The tool schemas the model sees, and how each one runs inside the sandbox. |
| 8 | `orchestrator/harness/agent/analyze.py` | Cheap facts collected before the model starts (languages, test command). |
| 9 | `orchestrator/harness/agent/loop.py` | **The heart.** `run()` is the five phases. `_tool_loop()` is the agent loop. `_deliver()` makes the patch. |
| 10 | `orchestrator/harness/llm/anthropic_llm.py` | The Claude call: streaming, adaptive thinking, strict tools, refusal fallback. |
| 11 | `orchestrator/harness/llm/scripted.py` | A fake model that replays a script, used for demos, tests and benchmarks. |
| 12 | `orchestrator/harness/orchestrator.py` | The queue, the workers, the run lifecycle, the metrics. |
| 13 | `orchestrator/harness/events.py` | The append-only event log with replay. That's what makes reconnects lossless. |
| 14 | `orchestrator/harness/api.py` | REST + SSE endpoints. |
| 15 | `web/src/lib/runView.ts` | Events → UI state. |
| 16 | `web/src/components/RunDetail.tsx` | The four tabs. |
| 17 | `orchestrator/tests/` | Each test reads like a spec of one feature. |

## 5. Follow one run through the system

Say you click **Launch**. This is what happens, with the file and function for each step:

1. **Browser** → `POST /api/runs` (`web/src/lib/api.ts: createRun`).
2. **API** validates the body as a `RunSpec` and calls `orch.submit()` (`api.py`).
3. **Orchestrator** creates a `Run`, opens its event log, emits `run.queued`, and puts the id on
   an `asyncio.Queue` (`orchestrator.py: submit`).
4. A **worker** coroutine takes it and calls `_execute(run)`.
5. **Provision.** `pool.acquire()` returns a running container. `cache.checkout()` makes sure the
   mirror exists and runs `git clone --shared` into the container's workspace folder. Ownership
   is handed to the sandbox user. A test `exec("true")` checks that it's ready. Then
   `sandbox.ready` is emitted with the timing breakdown.
6. **Analyze.** `analyze()` runs `git ls-files`, reads the manifests and detects `pytest`. The
   baseline tests run and their output streams out as `terminal` events.
7. **Generate.** `_tool_loop` sends the task and the analysis to Claude. Thinking deltas stream
   out as `agent.thinking`. For each tool call: `tool.call`, then it runs inside the sandbox, then
   `tool.result`. After any edit, a `diff` event carries the current patch.
8. **Test.** When the model calls `finish`, the harness runs the tests itself. If they fail, the
   output goes back to the model and step 7 repeats (at most `max_repair_rounds` times).
9. **Deliver.** `git commit` + `git format-patch` inside the sandbox. The patch is saved to
   `artifacts/<run>/change.patch`.
10. **Finish.** `run.completed` is emitted, the log is closed, and the sandbox is destroyed.
11. Meanwhile, the **browser's** `EventSource` received every event above within milliseconds and
    the reducer turned them into what you saw on screen.

## 6. The performance story

The goal is for an agent to be working within a few seconds of being requested. Where does the
time go?

| step | naive | this project |
|---|---|---|
| create + start a container | ~140 ms | **0 ms**: it was started in advance (warm pool) |
| get the code | full `git clone` over the network: **~2.5 s** for flask | `clone --shared` from a local mirror: **~100 ms** |
| install tools | minutes, if done at startup | **0**: baked into the prebuilt image |

The measured result, p50 time-to-ready for flask: **2.75 s cold → 359 ms cached → 151 ms warm**.

The concurrency benchmark shows the other side honestly. With 120 agents starting at the same
moment on a 4-core machine, startup p50 rises to 5.6 s, because the CPU is saturated. Being
honest about this is a strength in interviews. You can explain *why*: every command is a
`runc exec`, and 4 cores can only do so many per second. You can also explain *what you'd do
about it*: more hosts, caps on concurrent provisioning (already there), or lighter exec paths.
Read [docs/BENCHMARKS.md](docs/BENCHMARKS.md) and run `make bench-startup` yourself.

A good story to tell: the first Docker driver spawned the `docker` CLI for every command. At 40
agents, the CLI processes used more CPU than the actual work. Switching to Docker's HTTP API over
its unix socket removed that overhead. That's a real profiling → fix → re-measure loop.

## 7. Security

Think like an attacker who controls what the model writes:

- *"Delete everything on the host."* The agent can only see the container's filesystem. Root is
  read-only and only the workspace is mounted.
- *"Upload the code somewhere."* There's no network.
- *"Become root."* It runs as uid 1000 with no capabilities and `no-new-privileges`.
- *"Fork-bomb or eat all the RAM."* The PID and memory limits stop that.
- *"Trick the orchestrator itself."* This one is subtle. The workspace is a folder on the host.
  If the agent makes `link -> /etc/passwd` and then asks the harness to write `link`, and the
  harness wrote files directly on the host, it would overwrite a host file. So **all file I/O
  goes through `exec` inside the container**, where the link can only point at container files.
  On top of that, `safe_relpath` rejects `../` and absolute paths.
- *"Run forever."* Every command is wrapped in `timeout -s KILL`, and each run has an overall
  timeout.

What's *not* covered: containers share the host's kernel, so a kernel exploit could escape.
The next step is gVisor or Firecracker microVMs. Say this out loud in interviews. Knowing the
limits of your own design is what seniors look for.

## 8. Exercises to make it yours

Each one is a real feature. Do them in order, with a test for each.

1. **Add a tool.** Add a `git_log` tool that shows the last N commits. You'll touch
   `TOOL_DEFS` and `_dispatch` in `tools.py`, plus a test in `test_agent_loop.py`.
2. **Add a metric.** Track the average number of turns per successful run and show it in the
   header. Backend: `metrics()`. Frontend: `Header.tsx`.
3. **Per-run cost.** Use `input_tokens` and `output_tokens` to estimate dollars per run and show
   it in the run detail view.
4. **Persist runs.** Write `run.json` on every status change and reload finished runs at startup,
   so a restart doesn't empty the run list.
5. **Open a pull request.** After delivery, push the branch and open a PR through the GitHub API
   (the token stays outside the sandbox).
6. **A second sandbox backend.** Implement `SandboxProvider` with gVisor (`runtime: runsc`). It's
   mostly one config field, and then benchmark it against the current driver.

## 9. Talking about it in interviews

**30-second pitch.**
"I built an open-source harness that runs AI coding agents in parallel, each one in a locked-down
Docker sandbox. You give it a repo and a task. It spins up a sandbox, gets the code in from a
local git mirror, and runs an analyze, code, test, repair loop with Claude until the tests pass.
Then it hands back a patch. A React dashboard streams every agent's reasoning, terminal output
and diff live. I benchmarked it: 120 concurrent agents in Docker, and warm sandbox startup of
about 150 milliseconds, down from 2.75 seconds cold."

**Questions you'll get, and the honest answers.**

- *"How do you run 100+ agents?"* Asyncio. Each agent is a coroutine because it mostly waits on
  I/O. Sandboxes are driven through the Docker Engine API, so there's no process per command.
  The limit is CPU on the Docker host, and the benchmarks show where it bends.
- *"How is startup under 5 seconds?"* Three things: a prebuilt image, so nothing installs at
  boot; a warm pool of already-running containers; and git mirrors with `clone --shared`, so no
  network and no object copying. Single-run p50 is 151 ms. Under a 120-agent burst on 4 vCPUs it
  isn't under 5 s, and I can explain why and how to scale it.
- *"Why SSE and not WebSockets?"* The data flows one way. SSE gives automatic reconnection and
  `Last-Event-ID` resume, and our append-only log makes resume exact.
- *"How do you stop the agent breaking out?"* Walk through §7, including the symlink case and
  the kernel-sharing limitation.
- *"What would you change for production?"* Durable queue and state (Postgres/Redis), many
  Docker hosts with a scheduler, microVM isolation, per-repo dependency images, auth on the API,
  and PR delivery.
- *"Why a scripted model?"* So tests and benchmarks are deterministic and free, and so that
  benchmarks measure the harness rather than model latency.

**Matching each resume bullet to the code:**

| bullet | where it lives | proof |
|---|---|---|
| 100+ concurrent agents in isolated sandboxes | `orchestrator.py`, `sandbox/docker.py` | `test_runs_100_plus_agents_concurrently`, `test_docker_sandbox_is_isolated`, concurrency benchmark |
| autonomous loops: analysis → code → tests → patch | `agent/loop.py`, `agent/tools.py` | `test_loop_fixes_bug_and_delivers_patch`, `test_loop_repairs_after_failing_tests` |
| startup under 5 s via prebuilt images and repo caching | `sandbox-image/`, `sandbox/pool.py`, `repocache.py` | startup benchmark: 151 ms warm vs 2.75 s cold |
| React control plane streaming traces, reasoning, terminal, changes | `web/src/`, `api.py`, `events.py` | `test_api_run_lifecycle_and_sse`, screenshots in `docs/img/` |

## 10. Glossary

- **Agent loop**: model → tool call → result → model, repeated until done.
- **Alternates**: git's mechanism for borrowing objects from another repository.
- **Bare mirror**: a git repository with no working files, used as a cache.
- **Capability (Linux)**: a slice of root's power (e.g. `CAP_NET_ADMIN`). We drop all of them.
- **Coroutine**: a function that can pause at `await` and let others run.
- **Event loop**: the scheduler that runs coroutines in asyncio.
- **Exec**: running a new command inside an already-running container.
- **Image**: a read-only filesystem template that containers start from.
- **p50 / p95**: the median, and the value 95% of measurements fall under.
- **Patch**: a text description of a change that can be applied with `git am`.
- **SSE**: Server-Sent Events, a one-way server → browser stream over plain HTTP.
- **Warm pool**: containers started ahead of time and handed out on demand.
