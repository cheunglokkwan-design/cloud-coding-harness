from __future__ import annotations

from ..config import Settings
from .base import ExecResult, PathError, Sandbox, SandboxProvider, safe_relpath
from .docker import DockerProvider
from .local import LocalProvider
from .pool import WarmPool


def make_provider(settings: Settings) -> SandboxProvider:
    if settings.sandbox_driver == "docker":
        return DockerProvider(
            image=settings.sandbox_image,
            workspaces_dir=settings.workspaces_dir,
            mirrors_dir=settings.mirrors_dir,
            cpus=settings.sandbox_cpus,
            memory=settings.sandbox_memory,
            pids_limit=settings.sandbox_pids_limit,
            network=settings.sandbox_network,
            user=settings.sandbox_user,
            max_output=settings.max_output_bytes,
            exec_timeout=settings.exec_timeout_s,
        )
    return LocalProvider(settings.workspaces_dir, settings.max_output_bytes, settings.exec_timeout_s)


__all__ = [
    "ExecResult", "PathError", "Sandbox", "SandboxProvider", "WarmPool",
    "DockerProvider", "LocalProvider", "make_provider", "safe_relpath",
]
