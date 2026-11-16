"""Warm pool: keeps N started-but-unassigned sandboxes ready.

Acquiring from the pool skips container creation entirely, leaving only the repository
checkout (a ``git clone --shared`` from the local mirror) on the critical path. The pool
refills itself in the background after every acquisition.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .base import Sandbox, SandboxProvider

log = logging.getLogger(__name__)


class WarmPool:
    def __init__(self, provider: SandboxProvider, size: int, fill_concurrency: int = 4) -> None:
        self.provider = provider
        self.size = max(0, size)
        # Refills run in the background; capping them keeps a burst of acquisitions from
        # triggering a burst of container creations that competes with the runs themselves.
        self._fill_sem = asyncio.Semaphore(max(1, fill_concurrency))
        self._ready: asyncio.Queue[Sandbox] = asyncio.Queue()
        self._filling = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self.warm_hits = 0
        self.cold_starts = 0

    async def start(self, wait: bool = True) -> None:
        await self.provider.start()
        self._refill()
        if wait and self.size:
            # Wait for the initial fill so the first runs are warm (bounded).
            for _ in range(600):
                if self._ready.qsize() >= self.size or not self._tasks:
                    break
                await asyncio.sleep(0.05)

    def _refill(self) -> None:
        if self._closed:
            return
        missing = self.size - self._ready.qsize() - self._filling
        for _ in range(max(0, missing)):
            self._filling += 1
            t = asyncio.create_task(self._fill_one())
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    async def _fill_one(self) -> None:
        try:
            async with self._fill_sem:
                sb = await self.provider.create()
            sb.warm = True
            if self._closed:
                await sb.destroy()
            else:
                self._ready.put_nowait(sb)
        except Exception:  # pragma: no cover - logged and retried on next acquire
            log.exception("warm pool fill failed")
        finally:
            self._filling -= 1

    async def acquire(self) -> Sandbox:
        try:
            sb = self._ready.get_nowait()
            self.warm_hits += 1
        except asyncio.QueueEmpty:
            sb = await self.provider.create()
            sb.warm = False
            self.cold_starts += 1
        self._refill()
        return sb

    async def release(self, sb: Sandbox) -> None:
        # Sandboxes are single-use: a used workspace is never handed to another run.
        with contextlib.suppress(Exception):
            await sb.destroy()

    async def shutdown(self) -> None:
        self._closed = True
        for t in list(self._tasks):
            t.cancel()
        while not self._ready.empty():
            with contextlib.suppress(Exception):
                await self._ready.get_nowait().destroy()
        await self.provider.shutdown()

    def stats(self) -> dict[str, object]:
        return {
            **self.provider.stats(),
            "pool_target": self.size,
            "pool_ready": self._ready.qsize(),
            "warm_hits": self.warm_hits,
            "cold_starts": self.cold_starts,
        }
