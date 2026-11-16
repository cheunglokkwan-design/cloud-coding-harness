"""Docker sandbox driver.

Talks to the Docker Engine API directly over the unix socket (no ``docker`` CLI processes),
which matters at 100+ concurrent agents: spawning a CLI process per command costs far more
CPU than the command itself.

Each sandbox is a long-lived container started from a prebuilt image (``sandbox-image/``)
with a hardened profile:

  * no network (``NetworkMode: none`` by default)
  * all Linux capabilities dropped, ``no-new-privileges``, read-only root filesystem
  * CPU, memory and PID limits
  * non-root user
  * only two mounts: the run's workspace (rw) and the repository mirrors (ro, same path as
    on the host, so ``git clone --shared`` alternates resolve inside the container)

Commands are wrapped in ``timeout -s KILL`` so a runaway process inside the container is
killed even if the orchestrator loses the connection.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import shutil
import struct
import time
import uuid
from pathlib import Path

import httpx

from ..models import new_id
from .base import WORKDIR, ExecResult, OutputCallback, Sandbox, SandboxProvider, _Capture, run_process

LABEL_KEY = "cch.sandbox"
API_VERSION = "v1.43"
STDIN_CHUNK = 96_000  # bytes of base64 per exec argument, well under Linux MAX_ARG_STRLEN


class DockerError(RuntimeError):
    pass


def _docker_socket() -> str:
    host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    if not host.startswith("unix://"):
        raise DockerError("only unix:// DOCKER_HOST values are supported")
    return host[len("unix://"):]


class DockerAPI:
    def __init__(self, socket_path: str | None = None) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=socket_path or _docker_socket()),
            base_url=f"http://docker/{API_VERSION}",
            timeout=httpx.Timeout(60.0, read=None),
            limits=httpx.Limits(max_connections=512, max_keepalive_connections=64),
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise DockerError(f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text.strip()}")
        return r

    async def image_exists(self, image: str) -> bool:
        return (await self.client.get(f"/images/{image}/json")).status_code == 200

    async def list_containers(self, label: str) -> list[str]:
        r = await self._check(await self.client.get("/containers/json", params={
            "all": "true", "filters": f'{{"label":["{label}"]}}'}))
        return [c["Id"] for c in r.json()]

    async def create(self, name: str, config: dict) -> str:
        r = await self._check(await self.client.post("/containers/create", params={"name": name}, json=config))
        return r.json()["Id"]

    async def start(self, cid: str) -> None:
        await self._check(await self.client.post(f"/containers/{cid}/start"))

    async def remove(self, cid: str) -> None:
        r = await self.client.delete(f"/containers/{cid}", params={"force": "true", "v": "true"})
        if r.status_code not in (204, 404, 409):
            await self._check(r)

    async def exec(self, cid: str, cmd: list[str], on_output: OutputCallback | None, max_output: int,
                   ) -> tuple[int, str, str]:
        r = await self._check(await self.client.post(f"/containers/{cid}/exec", json={
            "AttachStdout": True, "AttachStderr": True, "Tty": False, "Cmd": cmd, "WorkingDir": WORKDIR}))
        eid = r.json()["Id"]
        out, err = _Capture(max_output), _Capture(max_output)
        async with self.client.stream("POST", f"/exec/{eid}/start", json={"Detach": False, "Tty": False}) as s:
            await self._check(s)
            buf = b""
            async for chunk in s.aiter_raw():
                buf += chunk
                # Demultiplex: 8-byte header [stream, 0, 0, 0, size(uint32 BE)] + payload.
                while len(buf) >= 8:
                    stream, size = buf[0], struct.unpack(">I", buf[4:8])[0]
                    if len(buf) < 8 + size:
                        break
                    text = buf[8:8 + size].decode(errors="replace")
                    buf = buf[8 + size:]
                    name = "stderr" if stream == 2 else "stdout"
                    (err if stream == 2 else out).add(text)
                    if on_output:
                        await on_output(name, text)
        info = (await self._check(await self.client.get(f"/exec/{eid}/json"))).json()
        code = info.get("ExitCode")
        return (code if code is not None else -1), out.value(), err.value()


class DockerSandbox(Sandbox):
    def __init__(self, sid: str, container: str, host_path: Path, api: DockerAPI, max_output: int,
                 default_timeout: float):
        self.id = sid
        self.container = container
        self.host_path = host_path
        self._api = api
        self._max_output = max_output
        self._timeout = default_timeout

    async def _stage_stdin(self, data: bytes) -> str:
        """Write stdin bytes to a temp file inside the container (exec has no stdin over HTTP)."""
        path = f"/tmp/.stdin-{uuid.uuid4().hex[:10]}"
        b64 = base64.b64encode(data).decode()
        chunks = [b64[i:i + STDIN_CHUNK] for i in range(0, len(b64), STDIN_CHUNK)] or [""]
        for i, part in enumerate(chunks):
            op = ">" if i == 0 else ">>"
            code, _, err = await self._api.exec(self.container, ["sh", "-c", f"printf %s '{part}' {op} {path}.b64"],
                                                None, 4096)
            if code != 0:
                raise OSError(f"staging stdin failed: {err}")
        return path

    async def exec(self, cmd, *, timeout=None, on_output: OutputCallback | None = None, stdin=None):
        t = timeout or self._timeout
        t0 = time.perf_counter()
        if stdin is not None:
            p = await self._stage_stdin(stdin)
            cmd = f"base64 -d {p}.b64 > {p} && rm -f {p}.b64 && ( {cmd} ) < {p}; rc=$?; rm -f {p}; exit $rc"
        argv = ["timeout", "-s", "KILL", str(int(max(1, t))), "sh", "-c", cmd]
        timed_out = False
        try:
            code, out, err = await asyncio.wait_for(
                self._api.exec(self.container, argv, on_output, self._max_output), t + 10)
        except TimeoutError:
            code, out, err, timed_out = -1, "", "", True
        ms = (time.perf_counter() - t0) * 1000
        if code == 137 and ms >= t * 1000 * 0.95:
            timed_out = True
        return ExecResult(code, out, err, ms, timed_out)

    async def destroy(self) -> None:
        with contextlib.suppress(Exception):
            await self._api.remove(self.container)
        await asyncio.to_thread(shutil.rmtree, self.host_path, True)


class DockerProvider(SandboxProvider):
    name = "docker"

    def __init__(
        self,
        *,
        image: str,
        workspaces_dir: Path,
        mirrors_dir: Path,
        cpus: float = 1.0,
        memory: str = "1g",
        pids_limit: int = 512,
        network: str = "none",
        user: str = "1000:1000",
        max_output: int = 64_000,
        exec_timeout: float = 300,
    ) -> None:
        self.image = image
        self.workspaces_dir = workspaces_dir
        self.mirrors_dir = mirrors_dir
        self.cpus = cpus
        self.memory = _parse_bytes(memory)
        self.pids_limit = pids_limit
        self.network = network
        # When the orchestrator is not root it cannot chown, so sandboxes run as its own uid.
        self.user = user if os.getuid() == 0 else f"{os.getuid()}:{os.getgid()}"
        self.max_output = max_output
        self.exec_timeout = exec_timeout
        self.created = 0
        self.instance = uuid.uuid4().hex[:8]
        self.api: DockerAPI | None = None

    async def start(self) -> None:
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        self.mirrors_dir.mkdir(parents=True, exist_ok=True)
        self.api = DockerAPI()
        try:
            exists = await self.api.image_exists(self.image)
        except httpx.TransportError as e:
            raise DockerError(f"cannot reach the Docker daemon: {e}") from e
        if not exists:
            raise DockerError(
                f"sandbox image {self.image!r} not found. Build it with `make image` "
                f"(docker build -t {self.image} sandbox-image)."
            )
        await self.reap()

    async def reap(self) -> None:
        """Remove sandboxes left behind by previous orchestrator processes."""
        assert self.api
        ids = await self.api.list_containers(f"{LABEL_KEY}=1")
        await asyncio.gather(*(self.api.remove(i) for i in ids), return_exceptions=True)

    async def shutdown(self) -> None:
        if self.api:
            await self.reap()
            await self.api.close()

    def _config(self, sid: str, ws: Path) -> dict:
        return {
            "Image": self.image,
            "Cmd": ["sleep", "infinity"],
            "User": self.user,
            "WorkingDir": WORKDIR,
            "Env": ["HOME=/tmp", "CI=1"],
            "Labels": {LABEL_KEY: "1", "cch.sandbox_id": sid, "cch.instance": self.instance},
            "NetworkDisabled": self.network == "none",
            "HostConfig": {
                "Init": True,
                "NetworkMode": self.network,
                "NanoCpus": int(self.cpus * 1e9),
                "Memory": self.memory,
                "MemorySwap": self.memory,
                "PidsLimit": self.pids_limit,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "ReadonlyRootfs": True,
                "Tmpfs": {"/tmp": "rw,exec,size=512m"},
                "Binds": [f"{ws}:{WORKDIR}", f"{self.mirrors_dir}:{self.mirrors_dir}:ro"],
            },
        }

    async def create(self) -> Sandbox:
        assert self.api, "provider not started"
        t0 = time.perf_counter()
        sid = new_id("sbx")
        ws = self.workspaces_dir / sid
        # The workspace stays owned by the orchestrator until checkout finishes; adopt_workspace
        # then hands it to the sandbox user.
        ws.mkdir(parents=True)
        name = f"cch-{sid}"
        try:
            cid = await self.api.create(name, self._config(sid, ws))
            await self.api.start(cid)
        except Exception:
            shutil.rmtree(ws, ignore_errors=True)
            with contextlib.suppress(Exception):
                await self.api.remove(name)
            raise
        self.created += 1
        sb = DockerSandbox(sid, cid, ws, self.api, self.max_output, self.exec_timeout)
        sb.container_ms = (time.perf_counter() - t0) * 1000
        return sb

    async def adopt_workspace(self, sandbox: Sandbox) -> None:
        if os.getuid() == 0:
            uid, _, gid = self.user.partition(":")
            r = await run_process(["chown", "-R", f"{uid}:{gid or uid}", str(sandbox.host_path)])
            if not r.ok:
                raise DockerError(f"chown failed: {r.stderr}")

    def stats(self) -> dict[str, object]:
        return {"driver": self.name, "image": self.image, "created": self.created}


def _parse_bytes(v: str) -> int:
    v = v.strip().lower()
    units = {"k": 1024, "m": 1024**2, "g": 1024**3}
    if v[-1:] in units:
        return int(float(v[:-1]) * units[v[-1]])
    if v.endswith("b"):
        return _parse_bytes(v[:-1])
    return int(v)
