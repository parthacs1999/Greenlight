from __future__ import annotations

from typing import Any

from google import genai
from google.genai import errors, types

from ..tools import Tool
from .base import Provider, ProviderError, ToolCall, ToolResult, Turn, Usage

DEFAULT_MODEL = "gemini-3.5-flash"
_SYNTHETIC_ID = "gemini-call-"


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, client: Any = None) -> None:
        self.model = model
        # Retry transient failures (rate limits, overload) with exponential backoff.
        retry = types.HttpRetryOptions(attempts=5, initial_delay=2.0, max_delay=30.0)
        self.client = client or genai.Client(
            api_key=api_key, http_options=types.HttpOptions(retry_options=retry)
        )
        self.config: types.GenerateContentConfig | None = None
        self.contents: list[types.Content] = []

    def start(self, system: str, tools: list[Tool], prompt: str) -> None:
        declarations = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters_json_schema=t.parameters
            )
            for t in tools
        ]
        self.config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=declarations)],
            # We run the loop ourselves so every tool call is visible and guarded.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        self.contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]

    def step(self) -> Turn:
        try:
            response = self.client.models.generate_content(
                model=self.model, contents=self.contents, config=self.config
            )
        except errors.APIError as exc:
            raise ProviderError(f"Gemini API error {exc.code}: {exc.message}") from exc

        candidate = response.candidates[0] if response.candidates else None
        if candidate is None or candidate.content is None:
            reason = getattr(candidate, "finish_reason", None) or getattr(
                response.prompt_feedback, "block_reason", "unknown"
            )
            raise ProviderError(f"Gemini returned no content (reason: {reason}).")

        # Append the model's content unchanged: it carries thought signatures.
        self.contents.append(candidate.content)
        parts = candidate.content.parts or []

        calls = []
        for i, part in enumerate(parts):
            fc = part.function_call
            if fc is not None:
                call_id = fc.id or f"{_SYNTHETIC_ID}{len(self.contents)}-{i}"
                calls.append(ToolCall(id=call_id, name=fc.name, args=dict(fc.args or {})))
        text = "".join(p.text for p in parts if p.text and not p.thought)

        meta = response.usage_metadata
        usage = Usage(
            input_tokens=(meta.prompt_token_count or 0) if meta else 0,
            cached_input_tokens=(meta.cached_content_token_count or 0) if meta else 0,
            output_tokens=((meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0))
            if meta
            else 0,
        )
        return Turn(
            text=text, tool_calls=calls, usage=usage, stop_reason=str(candidate.finish_reason)
        )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        parts = [
            types.Part(
                function_response=types.FunctionResponse(
                    id=None if r.call.id.startswith(_SYNTHETIC_ID) else r.call.id,
                    name=r.call.name,
                    response={"error": r.output} if r.is_error else {"output": r.output},
                )
            )
            for r in results
        ]
        self.contents.append(types.Content(role="user", parts=parts))
