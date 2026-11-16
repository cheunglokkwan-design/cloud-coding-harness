from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class RunStatus(StrEnum):
    queued = "queued"
    starting = "starting"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    error = "error"

    @property
    def terminal(self) -> bool:
        return self in {RunStatus.succeeded, RunStatus.failed, RunStatus.cancelled, RunStatus.error}


class Phase(StrEnum):
    provision = "provision"
    analyze = "analyze"
    generate = "generate"
    test = "test"
    deliver = "deliver"


class ScriptedStep(BaseModel):
    """One model turn for the deterministic ``scripted`` provider."""

    thinking: str | None = None
    text: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    delay_ms: int = 0


class AgentSpec(BaseModel):
    provider: Literal["anthropic", "scripted"] | None = None
    model: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    max_turns: int | None = None
    # Only for provider="scripted". If omitted, the agent reads .harness/script.json from the repo.
    script: list[ScriptedStep] | None = None


class RunSpec(BaseModel):
    repo: str = Field(description="Git URL, local git repo path, or plain local directory.")
    ref: str | None = Field(default=None, description="Branch, tag or commit. Defaults to HEAD.")
    task: str = Field(description="What the agent should do, in natural language.")
    test_command: str | None = Field(default=None, description="Overrides test command detection.")
    agent: AgentSpec = Field(default_factory=AgentSpec)
    labels: dict[str, str] = Field(default_factory=dict)


class BatchSpec(RunSpec):
    count: int = Field(default=1, ge=1, le=1000)


class StartupTiming(BaseModel):
    total_ms: float = 0
    container_ms: float = 0
    checkout_ms: float = 0
    warm: bool = False
    mirror_hit: bool = False


class Run(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    spec: RunSpec
    status: RunStatus = RunStatus.queued
    phase: Phase | None = None
    created_at: float = Field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    sandbox_id: str | None = None
    startup: StartupTiming | None = None
    tests_passed: bool | None = None
    summary: str | None = None
    error: str | None = None
    files_changed: int = 0
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def public(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Event(BaseModel):
    seq: int
    run_id: str
    ts: float = Field(default_factory=time.time)
    type: str
    phase: Phase | None = None
    data: dict[str, Any] = Field(default_factory=dict)
