from __future__ import annotations

from typing import Any

import anthropic

from ..tools import Tool
from .base import Provider, ProviderError, ToolCall, ToolResult, Turn, Usage

DEFAULT_MODEL = "claude-opus-5-5"

# Models that support server-side refusal fallbacks (`fallbacks: "default"`).
FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Block types that must not be echoed back if they precede a mid-output fallback.
_PRE_FALLBACK_DROP = frozenset({"thinking", "redacted_thinking", "tool_use"})


class AnthropicProvider(Provider):
    name = "claude"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        effort: str = "high",
        max_tokens: int = 16_000,
        client: Any = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.client = client or anthropic.Anthropic(api_key=api_key)
        self.system = ""
        self.tools: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    def start(self, system: str, tools: list[Tool], prompt: str) -> None:
        self.system = system
        self.tools = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]
        self.messages = [{"role": "user", "content": prompt}]

    def step(self) -> Turn:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system,
            "tools": self.tools,
            "messages": self.messages,
            "output_config": {"effort": self.effort},
            # The whole history is re-sent every step, so cache the growing prefix.
            "cache_control": {"type": "ephemeral"},
        }
        try:
            if self.model in FALLBACK_MODELS:
                response = self.client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **request
                )
            else:
                response = self.client.messages.create(**request)
        except anthropic.AuthenticationError as exc:
            raise ProviderError("Anthropic rejected the API key (401). Check your key.") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"Could not reach the Anthropic API: {exc}") from exc

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise ProviderError(f"Claude declined the request (category: {category}).")

        content = _echoable(response.content)
        self.messages.append({"role": "assistant", "content": content})

        calls = [
            ToolCall(id=b.id, name=b.name, args=dict(b.input))
            for b in content
            if b.type == "tool_use"
        ]
        text = "".join(b.text for b in content if b.type == "text")
        u = response.usage
        cache_read = u.cache_read_input_tokens or 0
        cache_write = u.cache_creation_input_tokens or 0
        usage = Usage(
            input_tokens=u.input_tokens + cache_read + cache_write,
            cached_input_tokens=cache_read,
            output_tokens=u.output_tokens,
        )
        return Turn(text=text, tool_calls=calls, usage=usage, stop_reason=response.stop_reason)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        # All results for one turn go back in a single user message.
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call.id,
                        "content": r.output,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        )


def _echoable(content: list[Any]) -> list[Any]:
    """Drop model-internal blocks that precede a mid-output fallback boundary."""
    last_fallback = max((i for i, b in enumerate(content) if b.type == "fallback"), default=-1)
    if last_fallback < 0:
        return list(content)
    return [
        b for i, b in enumerate(content) if i > last_fallback or b.type not in _PRE_FALLBACK_DROP
    ]
