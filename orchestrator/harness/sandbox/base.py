"""Sandbox abstraction.

A sandbox is a workspace (the repository checkout) plus an execution environment that can
run commands against it. The orchestrator never touches workspace files directly after the
initial checkout: reads, writes and git operations all go through ``exec`` so that anything
the agent does (including creating symlinks) is confined to the sandbox.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import posixpath
import signal
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

OutputCallback = Callable[[str, str], Awaitable[None]]

WORKDIR = "/workspace"


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def combined(self) -> str:
        out = self.stdout
        if self.stderr:
            out += ("\n" if out else "") + self.stderr
        if self.timed_out:
            out += "\n[command timed out]"
        return out


class PathError(ValueError):
    pass


def safe_relpath(path: str) -> str:
    """Normalise an agent-supplied path to a workspace-relative one, or raise."""
    if not path or "\x00" in path:
        raise PathError("empty or invalid path")
    p = path
    if p.startswith(WORKDIR + "/"):
        p = p[len(WORKDIR) + 1 :]
    if p.startswith("/"):
        raise PathError(f"absolute paths outside {WORKDIR} are not allowed: {path}")
    norm = posixpath.normpath(p)
    if norm == ".." or norm.startswith("../"):
        raise PathError(f"path escapes the workspace: {path}")
    return norm


def shquote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


@dataclass
class _Capture:
    limit: int
    chunks: list[str] = field(default_factory=list)
    size: int = 0
    dropped: int = 0

    def add(self, text: str) -> None:
        if self.size < self.limit:
            take = text[: self.limit - self.size]
            self.chunks.append(take)
            self.size += len(take)
            self.dropped += len(text) - len(take)
        else:
            self.dropped += len(text)

    def value(self) -> str:
        s = "".join(self.chunks)
        if self.dropped:
            s += f"\n... [{self.dropped} more bytes truncated]"
        return s


async def run_process(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stdin: bytes | None = None,
    timeout: float | None = None,
    on_output: OutputCallback | None = None,
    max_output: int = 64_000,
    new_session: bool = False,
) -> ExecResult:
    t0 = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=new_session,
    )
    out, err = _Capture(max_output), _Capture(max_output)

    async def pump(stream: asyncio.StreamReader, cap: _Capture, name: str) -> None:
        while True:
            data = await stream.read(8192)
            if not data:
                return
            text = data.decode(errors="replace")
            cap.add(text)
            if on_output:
                await on_output(name, text)

    async def feed() -> None:
        if stdin is not None and proc.stdin:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                proc.stdin.write(stdin)
                await proc.stdin.drain()
            proc.stdin.close()

    assert proc.stdout and proc.stderr
    work = asyncio.gather(feed(), pump(proc.stdout, out, "stdout"), pump(proc.stderr, err, "stderr"), proc.wait())
    timed_out = False
    try:
        await asyncio.wait_for(work, timeout)
    except TimeoutError:
        timed_out = True
        _kill(proc, new_session)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 5)
    except asyncio.CancelledError:
        _kill(proc, new_session)
        raise
    return ExecResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=out.value(),
        stderr=err.value(),
        duration_ms=(time.perf_counter() - t0) * 1000,
        timed_out=timed_out,
    )


def _kill(proc: asyncio.subprocess.Process, group: bool) -> None:
    with contextlib.suppress(ProcessLookupError):
        if group:
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()


class Sandbox(ABC):
    id: str
    host_path: Path
    warm: bool = False
    container_ms: float = 0.0

    @abstractmethod
    async def exec(
        self,
        cmd: str,
        *,
        timeout: float | None = None,
        on_output: OutputCallback | None = None,
        stdin: bytes | None = None,
    ) -> ExecResult: ...

    @abstractmethod
    async def destroy(self) -> None: ...

    # File helpers implemented on top of exec so they respect the sandbox boundary.
    async def read_file(self, path: str, max_bytes: int = 200_000) -> str:
        rel = safe_relpath(path)
        r = await self.exec(f"head -c {max_bytes} -- {shquote(rel)}", timeout=30)
        if not r.ok:
            raise FileNotFoundError(r.stderr.strip() or f"cannot read {rel}")
        return r.stdout

    async def write_file(self, path: str, content: str) -> None:
        rel = safe_relpath(path)
        parent = posixpath.dirname(rel) or "."
        cmd = f"mkdir -p -- {shquote(parent)} && cat > {shquote(rel)}"
        r = await self.exec(cmd, timeout=30, stdin=content.encode())
        if not r.ok:
            raise OSError(r.stderr.strip() or f"cannot write {rel}")


class SandboxProvider(ABC):
    name: str

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def shutdown(self) -> None: ...

    @abstractmethod
    async def create(self) -> Sandbox:
        """Return a running sandbox whose workspace directory exists but is empty."""

    async def adopt_workspace(self, sandbox: Sandbox) -> None:
        """Called after the orchestrator has checked the repository out into the workspace."""

    def stats(self) -> dict[str, object]:
        return {}
