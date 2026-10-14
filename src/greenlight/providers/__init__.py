from __future__ import annotations

from .base import Provider, ProviderError, ToolCall, ToolResult, Turn, Usage

PROVIDERS = ("claude", "gemini")


def create_provider(name: str, api_key: str, model: str | None = None, **options) -> Provider:
    """Build a provider. SDKs are imported lazily so one missing SDK doesn't break the other."""
    if name == "claude":
        from .anthropic_provider import DEFAULT_MODEL, AnthropicProvider

        return AnthropicProvider(api_key, model=model or DEFAULT_MODEL, **options)
    if name == "gemini":
        from .gemini_provider import DEFAULT_MODEL, GeminiProvider

        options.pop("effort", None)
        return GeminiProvider(api_key, model=model or DEFAULT_MODEL, **options)
    raise ValueError(f"Unknown provider '{name}'. Choose from: {', '.join(PROVIDERS)}")


__all__ = [
    "PROVIDERS",
    "Provider",
    "ProviderError",
    "ToolCall",
    "ToolResult",
    "Turn",
    "Usage",
    "create_provider",
]
