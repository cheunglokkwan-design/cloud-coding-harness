"""Static repository analysis run before the model is involved.

Cheap, deterministic facts (file inventory, languages, likely test command, README excerpt)
go into the first prompt so the model does not spend turns rediscovering them.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath

from ..sandbox.base import Sandbox

EXT_LANG = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".rb": "Ruby", ".php": "PHP",
    ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".cs": "C#", ".swift": "Swift", ".sh": "Shell",
}


@dataclass
class RepoAnalysis:
    file_count: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    test_command: str | None = None
    manifests: list[str] = field(default_factory=list)
    tree: list[str] = field(default_factory=list)
    readme_excerpt: str = ""
    head_commit: str = ""

    def to_prompt(self) -> str:
        langs = ", ".join(f"{k} ({v})" for k, v in self.languages.items()) or "unknown"
        parts = [
            f"Files: {self.file_count}. Languages: {langs}.",
            f"Manifests: {', '.join(self.manifests) or 'none'}.",
            f"Detected test command: {self.test_command or 'none detected'}.",
            "File tree (truncated):\n" + "\n".join(self.tree),
        ]
        if self.readme_excerpt:
            parts.append("README excerpt:\n" + self.readme_excerpt)
        return "\n\n".join(parts)

    def as_dict(self) -> dict:
        return asdict(self)


def detect_test_command(files: set[str], package_json: str | None, makefile: str | None) -> str | None:
    if package_json:
        try:
            if json.loads(package_json).get("scripts", {}).get("test"):
                return "npm test --silent"
        except ValueError:
            pass
    if any(f.endswith(".py") for f in files) and (
        {"pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"} & files
        or any(PurePosixPath(f).name.startswith("test_") for f in files)
    ):
        return "python -m pytest -q"
    if "go.mod" in files:
        return "go test ./..."
    if "Cargo.toml" in files:
        return "cargo test"
    if makefile and "\ntest:" in "\n" + makefile:
        return "make test"
    return None


async def analyze(sb: Sandbox) -> RepoAnalysis:
    r = await sb.exec("git ls-files", timeout=60)
    files = [f for f in r.stdout.splitlines() if f]
    fs = set(files)
    a = RepoAnalysis(file_count=len(files))
    langs = Counter(EXT_LANG[PurePosixPath(f).suffix] for f in files if PurePosixPath(f).suffix in EXT_LANG)
    a.languages = dict(langs.most_common(6))
    a.manifests = [m for m in ("package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml",
                               "pom.xml", "build.gradle", "Makefile", "Gemfile") if m in fs]

    async def maybe_read(name: str, n: int = 4000) -> str | None:
        if name not in fs:
            return None
        try:
            return (await sb.read_file(name, max_bytes=n))
        except FileNotFoundError:
            return None

    a.test_command = detect_test_command(fs, await maybe_read("package.json"), await maybe_read("Makefile"))
    readme = next((f for f in files if f.lower() in {"readme.md", "readme.rst", "readme.txt", "readme"}), None)
    if readme:
        a.readme_excerpt = (await maybe_read(readme, 1500)) or ""
    a.tree = files[:200] + ([f"... and {len(files) - 200} more"] if len(files) > 200 else [])
    a.head_commit = (await sb.exec("git rev-parse --short HEAD", timeout=10)).stdout.strip()
    return a
