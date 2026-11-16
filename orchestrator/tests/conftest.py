from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from harness.config import Settings

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples" / "demo-calc"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", sandbox_driver="local", warm_pool_size=0,
                    max_concurrency=8, mirror_refresh_s=0)


@pytest.fixture
def demo_repo() -> str:
    return str(DEMO)


@pytest.fixture
def tiny_repo(tmp_path: Path) -> Path:
    """A minimal git repo with one file, used for load-style tests."""
    repo = tmp_path / "tiny"
    repo.mkdir()
    (repo / "hello.txt").write_text("hello\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=repo, check=True, env=env)
    return repo


def docker_ready(image: str = "cch-sandbox:latest") -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    return r.returncode == 0
