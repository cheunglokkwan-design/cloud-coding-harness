"""Repository cache.

Each source repository is kept as a bare mirror under ``<data>/mirrors``. A run's workspace
is created with ``git clone --shared`` from that mirror, which writes only an
``objects/info/alternates`` pointer plus the checked-out files: no objects are copied and no
network is touched. That is what keeps the checkout part of sandbox startup in the tens of
milliseconds even for large repositories.

Sources can be:
  * a remote git URL (https://, git@, ssh://, file://)
  * a local git repository path
  * a plain local directory (snapshotted into a git repo keyed by content hash)
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "harness",
    "GIT_AUTHOR_EMAIL": "harness@localhost",
    "GIT_COMMITTER_NAME": "harness",
    "GIT_COMMITTER_EMAIL": "harness@localhost",
}


class GitError(RuntimeError):
    pass


async def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    env = {**os.environ, **GIT_ENV}
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {err.decode(errors='replace').strip()}")
    return out.decode(errors="replace")


@dataclass
class CheckoutResult:
    path: Path
    commit: str
    mirror_hit: bool
    duration_ms: float


def _is_remote(src: str) -> bool:
    return "://" in src or src.startswith("git@")


def _dir_fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in {".git", "__pycache__", "node_modules", ".pytest_cache"} for part in rel.parts):
            continue
        if p.is_file():
            st = p.stat()
            h.update(f"{rel}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


class RepoCache:
    def __init__(self, mirrors_dir: Path, refresh_s: float = 300.0) -> None:
        self.mirrors_dir = mirrors_dir
        self.refresh_s = refresh_s
        self._locks: dict[str, asyncio.Lock] = {}
        self._fresh_at: dict[str, float] = {}
        self.hits = 0
        self.misses = 0

    def _key(self, source: str) -> str:
        return hashlib.sha256(source.encode()).hexdigest()[:16]

    async def ensure_mirror(self, source: str) -> tuple[Path, bool]:
        """Return (mirror path, was_cached). Fetches only when missing or stale."""
        src = source if _is_remote(source) else str(Path(source).expanduser().resolve())
        key = self._key(src)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            mirror = self.mirrors_dir / f"{key}.git"
            if not _is_remote(src) and not (Path(src) / ".git").exists() and Path(src).is_dir():
                return await self._snapshot_dir(Path(src), key)
            if mirror.exists():
                stale = time.monotonic() - self._fresh_at.get(key, 0) > self.refresh_s
                if stale:
                    await git("remote", "update", "--prune", cwd=mirror)
                    self._fresh_at[key] = time.monotonic()
                self.hits += 1
                return mirror, True
            self.misses += 1
            tmp = mirror.with_suffix(".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            await git("clone", "--mirror", "--quiet", src, str(tmp))
            tmp.rename(mirror)
            self._fresh_at[key] = time.monotonic()
            return mirror, False

    async def _snapshot_dir(self, src: Path, key: str) -> tuple[Path, bool]:
        fp = _dir_fingerprint(src)
        mirror = self.mirrors_dir / f"{key}-{fp}.git"
        if mirror.exists():
            self.hits += 1
            return mirror, True
        self.misses += 1
        work = self.mirrors_dir / f"{key}-{fp}.snap"
        shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(src, work, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "node_modules"))
        await git("init", "--quiet", "-b", "main", cwd=work)
        await git("add", "-A", cwd=work)
        await git("commit", "--quiet", "-m", f"snapshot of {src.name}", cwd=work)
        await git("clone", "--mirror", "--quiet", str(work), str(mirror))
        shutil.rmtree(work, ignore_errors=True)
        return mirror, False

    async def checkout(self, source: str, ref: str | None, dest: Path, branch: str) -> CheckoutResult:
        t0 = time.perf_counter()
        mirror, hit = await self.ensure_mirror(source)
        # dest may already exist (e.g. a bind-mounted sandbox workspace) but must be empty.
        if dest.exists() and any(dest.iterdir()):
            raise GitError(f"workspace {dest} is not empty")
        # -c writes the identity for commits made inside the workspace into the new repo config.
        await git("clone", "--shared", "--quiet", "--no-checkout", "-c", "user.name=harness-agent",
                  "-c", "user.email=agent@harness.local", str(mirror), str(dest))
        target = ref or "HEAD"
        await git("checkout", "--quiet", "-B", branch, target, cwd=dest)
        commit = (await git("rev-parse", "HEAD", cwd=dest)).strip()
        return CheckoutResult(dest, commit, hit, (time.perf_counter() - t0) * 1000)
