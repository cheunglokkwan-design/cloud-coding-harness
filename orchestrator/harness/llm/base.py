from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# on_delta(kind, text) where kind is "thinking" or "text"
DeltaCallback = Callable[[str, str], Awaitable[None]]


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Turn:
    thinking: str = ""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    # Content to append verbatim as the assistant message (keeps thinking blocks intact).
    assistant_content: Any = None
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class LLM(ABC):
    name: str

    @abstractmethod
    async def turn(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_delta: DeltaCallback | None = None,
    ) -> Turn: ...
