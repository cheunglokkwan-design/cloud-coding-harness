"""Deterministic provider that replays a fixed list of turns.

Used for demos without an API key, for tests, and for load benchmarks where the goal is
to measure orchestration and sandbox overhead rather than model latency. ``delay_ms`` on a
step simulates model latency.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..models import ScriptedStep
from .base import LLM, DeltaCallback, ToolCall, Turn


class ScriptedLLM(LLM):
    name = "scripted"

    def __init__(self, steps: list[ScriptedStep]) -> None:
        self.steps = list(steps)
        self._i = 0
        self._n = 0

    async def turn(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_delta: DeltaCallback | None = None,
    ) -> Turn:
        if self._i >= len(self.steps):
            step = ScriptedStep(tool_calls=[{"name": "finish", "input": {"summary": "Script exhausted."}}])
        else:
            step = self.steps[self._i]
            self._i += 1
        if step.delay_ms:
            await asyncio.sleep(step.delay_ms / 1000)
        if on_delta:
            if step.thinking:
                await on_delta("thinking", step.thinking)
            if step.text:
                await on_delta("text", step.text)
        calls = []
        content: list[dict[str, Any]] = []
        if step.text:
            content.append({"type": "text", "text": step.text})
        for c in step.tool_calls:
            self._n += 1
            call = ToolCall(id=f"toolu_scripted_{self._n}", name=c["name"], input=dict(c.get("input", {})))
            calls.append(call)
            content.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})
        return Turn(
            thinking=step.thinking or "",
            text=step.text or "",
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            assistant_content=content or [{"type": "text", "text": "(no output)"}],
            model="scripted",
        )
