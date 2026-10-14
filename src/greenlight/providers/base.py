"""Provider-neutral interface for the agent loop.

Each provider keeps the conversation in its *native* format (Anthropic content
blocks, Gemini Contents). That matters: both APIs attach opaque data to model turns
(thinking blocks, thought signatures) that must be sent back unchanged, so
converting to a lowest-common-denominator format and back would silently break
multi-step reasoning.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..tools import Tool


class ProviderError(Exception):
    """The provider couldn't produce a usable turn (refusal, blocked, API error)."""


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    call: ToolCall
    output: str
    is_error: bool = False


@dataclass
class Usage:
    input_tokens: int = 0  # all prompt tokens, including cached ones
    cached_input_tokens: int = 0
    output_tokens: int = 0  # includes thinking tokens

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.output_tokens += other.output_tokens

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = ""


class Provider(ABC):
    name: str
    model: str

    @abstractmethod
    def start(self, system: str, tools: list[Tool], prompt: str) -> None:
        """Begin a new conversation."""

    @abstractmethod
    def step(self) -> Turn:
        """Ask the model for its next turn."""

    @abstractmethod
    def add_tool_results(self, results: list[ToolResult]) -> None:
        """Feed tool outputs back for the tool calls of the last turn."""
