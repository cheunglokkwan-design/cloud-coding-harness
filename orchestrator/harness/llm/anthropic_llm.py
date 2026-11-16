"""Claude via the official Anthropic Python SDK.

Uses streaming so thinking summaries and text reach the control plane as they are produced,
adaptive thinking with summarized display (so there is reasoning to show), and server-side
refusal fallbacks so a classifier decline on one model does not kill the run.
"""

from __future__ import annotations

from typing import Any

import anthropic

from .base import LLM, DeltaCallback, ToolCall, Turn

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLLM(LLM):
    name = "anthropic"

    def __init__(self, model: str, effort: str = "high", max_tokens: int = 32_000, client: Any = None) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.client = client or anthropic.AsyncAnthropic()

    async def turn(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_delta: DeltaCallback | None = None,
    ) -> Turn:
        async with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": self.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            async for event in stream:
                if on_delta is None or event.type != "content_block_delta":
                    continue
                delta = event.delta
                if delta.type == "thinking_delta":
                    await on_delta("thinking", delta.thinking)
                elif delta.type == "text_delta":
                    await on_delta("text", delta.text)
            final = await stream.get_final_message()

        turn = Turn(
            stop_reason=final.stop_reason or "end_turn",
            assistant_content=final.content,
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
            model=final.model,
        )
        for block in final.content:
            if block.type == "thinking":
                turn.thinking += block.thinking
            elif block.type == "text":
                turn.text += block.text
            elif block.type == "tool_use":
                inp = block.input if isinstance(block.input, dict) else {}
                turn.tool_calls.append(ToolCall(block.id, block.name, inp))
        if turn.stop_reason == "refusal" and getattr(final, "stop_details", None):
            turn.text += f"\n[refused: {final.stop_details.category}]"
        return turn
