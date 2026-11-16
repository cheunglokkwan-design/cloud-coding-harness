from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every field can be set with an env var prefixed ``HARNESS_``."""

    model_config = SettingsConfigDict(env_prefix="HARNESS_", env_file=".env", extra="ignore")

    # Where mirrors, workspaces, patches and run logs live. With the docker driver this
    # path must be identical on the host and inside the orchestrator container, because
    # it is bind-mounted into sandboxes (see docker-compose.yml).
    data_dir: Path = Path("/tmp/cch-data")

    # Scheduling
    max_concurrency: int = 128
    run_timeout_s: float = 900.0

    # Sandboxes
    sandbox_driver: Literal["docker", "local"] = "docker"
    sandbox_image: str = "cch-sandbox:latest"
    sandbox_cpus: float = 1.0
    sandbox_memory: str = "1g"
    sandbox_pids_limit: int = 512
    sandbox_network: str = "none"
    sandbox_user: str = "1000:1000"
    warm_pool_size: int = 8
    pool_fill_concurrency: int = 4
    # Max sandboxes being provisioned (container + checkout) at the same time. Bursts above
    # this queue briefly instead of thrashing the host; the wait counts toward startup time.
    provision_concurrency: int = 16
    exec_timeout_s: float = 300.0
    max_output_bytes: int = 64_000

    # Repository cache
    mirror_refresh_s: float = 300.0

    # Agent defaults
    default_provider: Literal["anthropic", "scripted"] = "anthropic"
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    max_agent_turns: int = 40
    max_repair_rounds: int = 2

    # Control plane
    host: str = "0.0.0.0"
    port: int = 8080
    web_dist: Path | None = None
    cors_origins: list[str] = ["http://localhost:5173"]

    @property
    def mirrors_dir(self) -> Path:
        return self.data_dir / "mirrors"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    def ensure_dirs(self) -> None:
        for d in (self.mirrors_dir, self.workspaces_dir, self.artifacts_dir):
            d.mkdir(parents=True, exist_ok=True)
