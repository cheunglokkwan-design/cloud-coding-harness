"""The autonomous execution loop: analyze -> generate -> test (-> repair) -> deliver.

The loop is model-agnostic: it talks to an ``LLM`` (Claude or the scripted provider) and a
``Sandbox`` and reports everything it does through ``emit`` so the control plane can show
it live.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..events import clip
from ..llm.base import LLM, Turn
from ..models import Phase
from ..sandbox.base import Sandbox
from .analyze import RepoAnalysis, analyze
from .tools import TOOL_DEFS, ToolExecutor

Emit = Callable[..., Awaitable[Any]]

SYSTEM_PROMPT = """You are an autonomous software engineer working inside an isolated sandbox that \
contains a git checkout of the user's repository. There is no network access.

Work like a careful senior engineer:
- Read the relevant code before you change it. Prefer small, targeted edits (edit_file) over rewriting files.
- Run the project's tests or a quick reproduction with run_command to check your work.
- Do not modify tests to make them pass unless the task asks for it.
- Keep the change focused on the task; do not reformat unrelated code.
When the task is complete, call finish with a short summary of what you changed and why. \
After you finish, the harness runs the test suite; if it fails you will get the output and a chance to fix it."""


@dataclass
class LoopResult:
    tests_passed: bool | None
    summary: str
    files_changed: int
    patch_path: Path | None
    turns: int
    input_tokens: int = 0
    output_tokens: int = 0
    analysis: RepoAnalysis | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class _Coalescer:
    """Batches streaming deltas so the UI gets smooth updates without one event per token."""

    def __init__(self, emit: Emit, interval: float = 0.15, max_chars: int = 600) -> None:
        self.emit = emit
        self.interval = interval
        self.max_chars = max_chars
        self.buf: dict[str, str] = {}
        self.last = time.monotonic()

    async def add(self, kind: str, text: str) -> None:
        self.buf[kind] = self.buf.get(kind, "") + text
        if time.monotonic() - self.last > self.interval or len(self.buf[kind]) > self.max_chars:
            await self.flush()

    async def flush(self) -> None:
        for kind, text in self.buf.items():
            if text:
                await self.emit(f"agent.{kind}", Phase.generate, delta=text)
        self.buf.clear()
        self.last = time.monotonic()


class AgentLoop:
    def __init__(
        self,
        *,
        sandbox: Sandbox,
        llm: LLM,
        task: str,
        emit: Emit,
        artifacts_dir: Path,
        test_command: str | None = None,
        max_turns: int = 40,
        max_repair_rounds: int = 2,
        test_timeout: float = 600,
    ) -> None:
        self.sb = sandbox
        self.llm = llm
        self.task = task
        self.emit = emit
        self.artifacts_dir = artifacts_dir
        self.test_command_override = test_command
        self.max_turns = max_turns
        self.max_repair_rounds = max_repair_rounds
        self.test_timeout = test_timeout
        self.turns = 0
        self.in_tokens = 0
        self.out_tokens = 0
        self._cmd_seq = 0
        self.tools = ToolExecutor(sandbox, self._command_started, self._command_finished)

    # --- terminal plumbing -------------------------------------------------
    async def _command_started(self, command: str):
        self._cmd_seq += 1
        cid = f"cmd{self._cmd_seq}"
        phase = self._phase
        await self.emit("command.started", phase, command_id=cid, command=command)

        async def on_output(stream: str, chunk: str) -> None:
            await self.emit("terminal", phase, command_id=cid, stream=stream, chunk=clip(chunk))

        return cid, on_output

    async def _command_finished(self, cid: str, code: int, ms: float) -> None:
        await self.emit("command.finished", self._phase, command_id=cid, exit_code=code, duration_ms=round(ms, 1))

    # --- phases -------------------------------------------------------------
    async def _enter(self, phase: Phase) -> float:
        self._phase = phase
        await self.emit("phase.started", phase)
        return time.perf_counter()

    async def _leave(self, phase: Phase, t0: float, **data: Any) -> None:
        await self.emit("phase.completed", phase, duration_ms=round((time.perf_counter() - t0) * 1000, 1), **data)

    async def run(self) -> LoopResult:
        self._phase = Phase.analyze
        t0 = await self._enter(Phase.analyze)
        analysis = await analyze(self.sb)
        test_cmd = self.test_command_override or analysis.test_command
        analysis.test_command = test_cmd
        await self.emit("analysis", Phase.analyze, **analysis.as_dict())
        baseline = None
        if test_cmd:
            baseline = await self._run_tests(test_cmd, baseline=True)
        await self._leave(Phase.analyze, t0)

        messages: list[dict[str, Any]] = [{"role": "user", "content": self._initial_prompt(analysis, baseline)}]
        summary = ""
        tests_passed: bool | None = None
        pending: list[dict[str, Any]] = []
        for round_ in range(self.max_repair_rounds + 1):
            t0 = await self._enter(Phase.generate)
            finished, summary_now, pending = await self._tool_loop(messages, pending)
            summary = summary_now or summary
            await self._leave(Phase.generate, t0, round=round_, finished=finished)
            await self._emit_diff()

            if not test_cmd:
                break
            t0 = await self._enter(Phase.test)
            res = await self._run_tests(test_cmd)
            tests_passed = res["passed"]
            await self._leave(Phase.test, t0, passed=tests_passed)
            if tests_passed or self.turns >= self.max_turns or round_ == self.max_repair_rounds:
                break
            feedback = (
                f"The harness ran `{test_cmd}` after your changes and it failed (exit code {res['exit_code']}).\n"
                f"Output:\n```\n{clip(res['output'], 12_000)}\n```\nPlease fix the problem, then call finish again."
            )
            messages.append({"role": "user", "content": [*pending, {"type": "text", "text": feedback}]})
            pending = []

        t0 = await self._enter(Phase.deliver)
        patch_path, files_changed = await self._deliver(summary)
        await self._leave(Phase.deliver, t0, files_changed=files_changed)
        return LoopResult(tests_passed, summary, files_changed, patch_path, self.turns,
                          self.in_tokens, self.out_tokens, analysis)

    def _initial_prompt(self, analysis: RepoAnalysis, baseline: dict[str, Any] | None) -> str:
        base = ""
        if baseline is not None:
            state = "pass" if baseline["passed"] else f"FAIL (exit code {baseline['exit_code']})"
            base = (f"\n\n<baseline_tests command=\"{analysis.test_command}\">Before any change the tests {state}.\n"
                    f"{clip(baseline['output'], 6000)}\n</baseline_tests>")
        return (f"<task>\n{self.task}\n</task>\n\n<repository>\n{analysis.to_prompt()}\n</repository>{base}\n\n"
                "Complete the task, then call finish.")

    async def _tool_loop(self, messages: list[dict[str, Any]], pending: list[dict[str, Any]]):
        """Run model turns until finish/end_turn/turn budget. Returns (finished, summary, pending_results)."""
        if pending:
            messages.append({"role": "user", "content": pending})
        coalescer = _Coalescer(self.emit)
        while self.turns < self.max_turns:
            self.turns += 1
            await self.emit("agent.turn", Phase.generate, turn=self.turns)
            turn: Turn = await self.llm.turn(SYSTEM_PROMPT, messages, TOOL_DEFS, coalescer.add)
            await coalescer.flush()
            self.in_tokens += turn.input_tokens
            self.out_tokens += turn.output_tokens
            await self.emit("agent.message", Phase.generate, turn=self.turns, thinking=turn.thinking,
                            text=turn.text, stop_reason=turn.stop_reason, model=turn.model,
                            input_tokens=turn.input_tokens, output_tokens=turn.output_tokens)
            messages.append({"role": "assistant", "content": turn.assistant_content})

            if turn.stop_reason == "refusal":
                return False, "The model declined this request.", []
            if not turn.tool_calls:
                if turn.stop_reason == "max_tokens":
                    messages.append({"role": "user", "content": "Continue."})
                    continue
                return False, turn.text.strip(), []

            results: list[dict[str, Any]] = []
            modified = False
            finished, summary = False, ""
            for call in turn.tool_calls:
                await self.emit("tool.call", Phase.generate, id=call.id, name=call.name,
                                input={k: clip(str(v), 4000) for k, v in call.input.items()})
                t0 = time.perf_counter()
                out = await self.tools.call(call.name, call.input)
                await self.emit("tool.result", Phase.generate, id=call.id, name=call.name, output=clip(out.output, 8000),
                                is_error=out.is_error, duration_ms=round((time.perf_counter() - t0) * 1000, 1))
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": clip(out.output, 30_000), "is_error": out.is_error})
                modified |= out.modified
                if out.finished:
                    finished, summary = True, out.summary or ""
            if modified:
                await self._emit_diff()
            if finished:
                return True, summary, results
            messages.append({"role": "user", "content": results})
        await self.emit("agent.budget_exhausted", Phase.generate, max_turns=self.max_turns)
        return False, "Stopped: turn budget exhausted.", []

    async def _run_tests(self, cmd: str, baseline: bool = False) -> dict[str, Any]:
        code, out, timed_out = await self.tools.run_shell(cmd, self.test_timeout)
        # pytest exit code 5 means "no tests collected"; treat as pass so empty suites don't loop.
        passed = code == 0 or (code == 5 and "pytest" in cmd)
        res = {"passed": passed, "exit_code": code, "timed_out": timed_out, "output": out, "baseline": baseline}
        await self.emit("test.result", self._phase, command=cmd, passed=passed, exit_code=code,
                        timed_out=timed_out, baseline=baseline, output=clip(out, 8000))
        return res

    async def _diff(self) -> tuple[str, str]:
        await self.sb.exec("git add -A", timeout=60)
        patch = await self.sb.exec("git diff --cached --binary HEAD", timeout=60)
        stat = await self.sb.exec("git diff --cached --numstat HEAD", timeout=60)
        return patch.stdout, stat.stdout

    async def _emit_diff(self) -> None:
        patch, numstat = await self._diff()
        files = []
        for line in numstat.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                files.append({"path": parts[2], "added": parts[0], "removed": parts[1]})
        await self.emit("diff", self._phase, patch=clip(patch, 200_000), files=files)

    async def _deliver(self, summary: str) -> tuple[Path | None, int]:
        patch, numstat = await self._diff()
        files_changed = len([line for line in numstat.splitlines() if line.strip()])
        if not patch.strip():
            await self.emit("delivery", Phase.deliver, patch_path=None, files_changed=0)
            return None, 0
        title = self.task.strip().splitlines()[0][:72]
        msg = f"{title}\n\n{summary}\n\nGenerated by cloud-coding-harness."
        await self.sb.exec("git commit -q -F -", timeout=60, stdin=msg.encode())
        mbox = await self.sb.exec("git format-patch --stdout HEAD~1", timeout=60)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifacts_dir / "change.patch"
        await asyncio.to_thread(path.write_text, mbox.stdout or patch, "utf-8")
        await asyncio.to_thread((self.artifacts_dir / "summary.json").write_text,
                                json.dumps({"summary": summary, "files_changed": files_changed}), "utf-8")
        await self.emit("delivery", Phase.deliver, patch_path=str(path), files_changed=files_changed,
                        apply_hint="git am < change.patch")
        return path, files_changed
