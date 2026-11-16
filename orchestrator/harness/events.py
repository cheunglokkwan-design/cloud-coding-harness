"""In-process event log with replay + live fan-out.

Every run gets an append-only list of events. Subscribers read from any sequence number
and then block until new events arrive, which makes SSE reconnects (Last-Event-ID) lossless.
Events are also appended to ``<artifacts>/<run_id>/events.jsonl`` so finished runs survive
an orchestrator restart for inspection.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .models import Event, Phase

# Terminal chunks can be very chatty; cap each event's payload so the UI stays responsive.
MAX_CHUNK = 16_000


class RunLog:
    def __init__(self, run_id: str, path: Path | None) -> None:
        self.run_id = run_id
        self.events: list[Event] = []
        self.closed = False
        self._cond = asyncio.Condition()
        self._fh = path.open("a", encoding="utf-8") if path else None

    async def append(self, type_: str, phase: Phase | None, data: dict[str, Any]) -> Event:
        async with self._cond:
            ev = Event(seq=len(self.events) + 1, run_id=self.run_id, type=type_, phase=phase, data=data)
            self.events.append(ev)
            if self._fh:
                self._fh.write(ev.model_dump_json() + "\n")
                self._fh.flush()
            self._cond.notify_all()
            return ev

    async def close(self) -> None:
        async with self._cond:
            self.closed = True
            if self._fh:
                self._fh.close()
                self._fh = None
            self._cond.notify_all()

    async def follow(self, after: int = 0) -> AsyncIterator[Event]:
        idx = after
        while True:
            async with self._cond:
                while idx >= len(self.events) and not self.closed:
                    await self._cond.wait()
                batch = self.events[idx:]
                done = self.closed
            for ev in batch:
                yield ev
            idx += len(batch)
            if done and idx >= len(self.events):
                return


class EventBus:
    """Holds one RunLog per run plus a global feed of run status changes."""

    def __init__(self, artifacts_dir: Path | None = None) -> None:
        self._logs: dict[str, RunLog] = {}
        self._artifacts = artifacts_dir
        self._global: set[asyncio.Queue[dict[str, Any]]] = set()

    def open(self, run_id: str) -> RunLog:
        path = None
        if self._artifacts:
            d = self._artifacts / run_id
            d.mkdir(parents=True, exist_ok=True)
            path = d / "events.jsonl"
        log = RunLog(run_id, path)
        self._logs[run_id] = log
        return log

    def get(self, run_id: str) -> RunLog | None:
        return self._logs.get(run_id)

    async def emit(self, run_id: str, type_: str, phase: Phase | None = None, **data: Any) -> Event:
        log = self._logs[run_id]
        return await log.append(type_, phase, data)

    # Global feed (run list / metrics updates)
    def subscribe_global(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10_000)
        self._global.add(q)
        return q

    def unsubscribe_global(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._global.discard(q)

    def publish_global(self, msg: dict[str, Any]) -> None:
        for q in list(self._global):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # Slow consumer: drop it rather than block the orchestrator.
                self._global.discard(q)


def clip(text: str, limit: int = MAX_CHUNK) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + f"\n... [{len(text) - limit} bytes elided] ...\n" + text[-limit // 2 :]


def dumps(ev: Event) -> str:
    return json.dumps(ev.model_dump(mode="json"), separators=(",", ":"))
