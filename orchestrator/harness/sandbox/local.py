"""Local process sandbox.

Runs commands as plain subprocesses in a per-run directory. This provides *workspace*
separation only; it is NOT an isolation boundary. Use it for development, tests and for
measuring orchestrator overhead without Docker. Production use should run the docker driver.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from ..models import new_id
from .base import OutputCallback, Sandbox, SandboxProvider, run_process


class LocalSandbox(Sandbox):
    def __init__(self, sid: str, host_path: Path, max_output: int, default_timeout: float) -> None:
        self.id = sid
        self.host_path = host_path
        self._max_output = max_output
        self._timeout = default_timeout
        self._env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(host_path / ".home"),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "CI": "1",
        }

    async def exec(self, cmd, *, timeout=None, on_output: OutputCallback | None = None, stdin=None):
        return await run_process(
            ["sh", "-c", cmd],
            cwd=str(self.host_path),
            env=self._env,
            stdin=stdin,
            timeout=timeout or self._timeout,
            on_output=on_output,
            max_output=self._max_output,
            new_session=True,
        )

    async def destroy(self) -> None:
        shutil.rmtree(self.host_path, ignore_errors=True)


class LocalProvider(SandboxProvider):
    name = "local"

    def __init__(self, workspaces_dir: Path, max_output: int = 64_000, exec_timeout: float = 300) -> None:
        self.workspaces_dir = workspaces_dir
        self.max_output = max_output
        self.exec_timeout = exec_timeout
        self.created = 0

    async def start(self) -> None:
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    async def shutdown(self) -> None:
        return None

    async def create(self) -> Sandbox:
        t0 = time.perf_counter()
        sid = new_id("sbx")
        path = self.workspaces_dir / sid
        path.mkdir(parents=True)
        self.created += 1
        sb = LocalSandbox(sid, path, self.max_output, self.exec_timeout)
        sb.container_ms = (time.perf_counter() - t0) * 1000
        return sb

    async def adopt_workspace(self, sandbox: Sandbox) -> None:
        # Keep HOME out of `git status` noise.
        home = sandbox.host_path / ".home"
        home.mkdir(exist_ok=True)
        exclude = sandbox.host_path / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a") as fh:
            fh.write("\n.home/\n")

    def stats(self) -> dict[str, object]:
        return {"driver": self.name, "created": self.created}
