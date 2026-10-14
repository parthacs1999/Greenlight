"""Provider adapters, tested against fake SDK clients (no network, no API key)."""

from types import SimpleNamespace as NS

from google.genai import types

from greenlight.providers import create_provider
from greenlight.providers.anthropic_provider import AnthropicProvider
from greenlight.providers.base import ToolResult
from greenlight.providers.gemini_provider import GeminiProvider
from greenlight.tools import Tool

TOOLS = [Tool("read_file", "Read a file", {"type": "object", "properties": {}}, lambda: "")]


class FakeAnthropicClient:
    def __init__(self, response):
        self.response = response
        self.calls = []
        create = self._create
        self.messages = NS(create=lambda **kw: create("standard", kw))
        self.beta = NS(messages=NS(create=lambda **kw: create("beta", kw)))

    def _create(self, endpoint, kwargs):
        self.calls.append((endpoint, kwargs))
        return self.response


def anthropic_response(content, stop_reason="tool_use"):
    usage = NS(
        input_tokens=10, output_tokens=5, cache_read_input_tokens=100, cache_creation_input_tokens=0
    )
    return NS(content=content, stop_reason=stop_reason, usage=usage, stop_details=None)


def test_anthropic_request_shape_and_tool_round_trip():
    tool_use = NS(type="tool_use", id="tu_1", name="read_file", input={"path": "a.py"})
    thinking = NS(type="thinking", thinking="", signature="sig")
    client = FakeAnthropicClient(anthropic_response([thinking, tool_use]))
    provider = AnthropicProvider("key", client=client)
    provider.start("system", TOOLS, "fix it")

    turn = provider.step()

    endpoint, request = client.calls[0]
    assert endpoint == "beta"  # Opus 5.5 opts into server-side refusal fallbacks
    assert request["fallbacks"] == "default"
    assert request["model"] == "claude-opus-5-5"
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["tools"][0]["input_schema"] == TOOLS[0].parameters
    assert turn.tool_calls[0].args == {"path": "a.py"}
    assert turn.usage.input_tokens == 110 and turn.usage.cached_input_tokens == 100
    # Thinking blocks are echoed back unchanged.
    assert provider.messages[-1]["content"] == [thinking, tool_use]

    provider.add_tool_results([ToolResult(turn.tool_calls[0], "contents", False)])
    result_block = provider.messages[-1]["content"][0]
    assert result_block == {
        "type": "tool_result",
        "tool_use_id": "tu_1",
        "content": "contents",
        "is_error": False,
    }


def test_anthropic_non_fallback_model_uses_standard_endpoint():
    client = FakeAnthropicClient(anthropic_response([NS(type="text", text="hi")], "end_turn"))
    provider = AnthropicProvider("key", model="claude-haiku-5-5", client=client)
    provider.start("s", TOOLS, "p")
    assert provider.step().text == "hi"
    assert client.calls[0][0] == "standard"


def test_anthropic_drops_blocks_before_fallback_boundary():
    content = [
        NS(type="thinking", thinking=""),
        NS(type="text", text="partial "),
        NS(type="fallback"),
        NS(type="text", text="rest"),
    ]
    client = FakeAnthropicClient(anthropic_response(content, "end_turn"))
    provider = AnthropicProvider("key", client=client)
    provider.start("s", TOOLS, "p")
    provider.step()
    assert [b.type for b in provider.messages[-1]["content"]] == ["text", "fallback", "text"]


def test_gemini_function_call_round_trip():
    content = types.Content(
        role="model",
        parts=[types.Part(function_call=types.FunctionCall(id="fc1", name="read_file", args={}))],
    )
    response = types.GenerateContentResponse(
        candidates=[types.Candidate(content=content)],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=50, candidates_token_count=7, thoughts_token_count=3
        ),
    )
    calls = []
    client = NS(models=NS(generate_content=lambda **kw: calls.append(kw) or response))
    provider = GeminiProvider("key", client=client)
    provider.start("system", TOOLS, "fix it")

    turn = provider.step()

    assert calls[0]["config"].automatic_function_calling.disable is True
    assert turn.tool_calls[0].name == "read_file"
    assert turn.usage.input_tokens == 50 and turn.usage.output_tokens == 10
    provider.add_tool_results([ToolResult(turn.tool_calls[0], "data", False)])
    fr = provider.contents[-1].parts[0].function_response
    assert fr.id == "fc1" and fr.response == {"output": "data"}


def test_factory_defaults():
    assert create_provider("claude", "k").model == "claude-opus-5-5"
    assert create_provider("gemini", "k", effort="high").model == "gemini-3.5-flash"
