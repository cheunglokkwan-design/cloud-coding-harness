"""Tools the coding agent can call. Every tool executes inside the run's sandbox."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..sandbox.base import OutputCallback, PathError, Sandbox, safe_relpath, shquote


def _tool(name: str, description: str, props: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": props,
            "required": list(props),
            "additionalProperties": False,
        },
    }


S = {"type": "string"}

TOOL_DEFS: list[dict[str, Any]] = [
    _tool("list_files", "List files tracked or created in the repository under a directory ('.' for the root).",
          {"path": S}),
    _tool("read_file", "Read a text file from the repository. Paths are relative to the repository root.",
          {"path": S}),
    _tool("write_file", "Create or overwrite a file with the given full content.", {"path": S, "content": S}),
    _tool("edit_file",
          "Replace exactly one occurrence of old_str with new_str in a file. old_str must match exactly "
          "(including whitespace) and be unique in the file.",
          {"path": S, "old_str": S, "new_str": S}),
    _tool("search", "Search tracked files with a regular expression (git grep -E). Use '.' as path for the whole repo.",
          {"pattern": S, "path": S}),
    _tool("run_command",
          "Run a shell command in the repository root inside the sandbox (no network). Use it to run tests, "
          "linters or inspect the project. timeout_s is capped at 600.",
          {"command": S, "timeout_s": {"type": "integer"}}),
    _tool("finish", "Call when the task is complete. The harness will then run the test suite and deliver a patch.",
          {"summary": S}),
]


@dataclass
class ToolOutcome:
    output: str
    is_error: bool = False
    modified: bool = False
    finished: bool = False
    summary: str | None = None


CommandHook = Callable[[str], Awaitable[Callable[[str, str], Awaitable[None]] | None]]


class ToolExecutor:
    def __init__(self, sandbox: Sandbox, on_command: Callable[[str], Awaitable[tuple[str, OutputCallback]]],
                 on_command_done: Callable[[str, int, float], Awaitable[None]]) -> None:
        self.sb = sandbox
        self.on_command = on_command
        self.on_command_done = on_command_done

    async def run_shell(self, command: str, timeout: float) -> tuple[int, str, bool]:
        cid, cb = await self.on_command(command)
        t0 = time.perf_counter()
        r = await self.sb.exec(command, timeout=timeout, on_output=cb)
        await self.on_command_done(cid, r.exit_code, (time.perf_counter() - t0) * 1000)
        return r.exit_code, r.combined(), r.timed_out

    async def call(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        try:
            return await self._dispatch(name, args)
        except PathError as e:
            return ToolOutcome(f"error: {e}", is_error=True)
        except (OSError, FileNotFoundError) as e:
            return ToolOutcome(f"error: {e}", is_error=True)
        except KeyError as e:
            return ToolOutcome(f"error: missing argument {e}", is_error=True)

    async def _dispatch(self, name: str, a: dict[str, Any]) -> ToolOutcome:
        if name == "list_files":
            rel = safe_relpath(a.get("path") or ".")
            r = await self.sb.exec(
                f"git ls-files -co --exclude-standard -- {shquote(rel)} | head -n 1000", timeout=30)
            return ToolOutcome(r.stdout or "(no files)", is_error=not r.ok)
        if name == "read_file":
            content = await self.sb.read_file(a["path"])
            numbered = "\n".join(f"{i + 1:5d}  {line}" for i, line in enumerate(content.splitlines()))
            return ToolOutcome(numbered or "(empty file)")
        if name == "write_file":
            await self.sb.write_file(a["path"], a["content"])
            return ToolOutcome(f"wrote {safe_relpath(a['path'])} ({len(a['content'])} bytes)", modified=True)
        if name == "edit_file":
            path, old, new = a["path"], a["old_str"], a["new_str"]
            content = await self.sb.read_file(path, max_bytes=5_000_000)
            n = content.count(old) if old else 0
            if n != 1:
                return ToolOutcome(f"error: old_str found {n} times in {path}; it must match exactly once",
                                   is_error=True)
            await self.sb.write_file(path, content.replace(old, new, 1))
            return ToolOutcome(f"edited {safe_relpath(path)}", modified=True)
        if name == "search":
            rel = safe_relpath(a.get("path") or ".")
            r = await self.sb.exec(
                f"git grep -n -I -E -e {shquote(a['pattern'])} -- {shquote(rel)} | head -n 200", timeout=30)
            return ToolOutcome(r.stdout or "(no matches)")
        if name == "run_command":
            timeout = float(min(max(int(a.get("timeout_s") or 120), 1), 600))
            code, out, timed_out = await self.run_shell(a["command"], timeout)
            header = f"exit_code={code}" + (" (timed out)" if timed_out else "")
            return ToolOutcome(f"{header}\n{out}", is_error=code != 0, modified=True)
        if name == "finish":
            return ToolOutcome("ok", finished=True, summary=a.get("summary", ""))
        return ToolOutcome(f"error: unknown tool {name}", is_error=True)
