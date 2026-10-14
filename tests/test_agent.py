from greenlight.agent import Agent, Budget
from greenlight.pricing import Price
from greenlight.providers.base import ProviderError, ToolCall, Turn, Usage
from greenlight.tools import Workspace

from .conftest import ScriptedProvider


def call(name, **args):
    return ToolCall(id=f"id-{name}", name=name, args=args)


def turn(*calls, text="", tokens=100):
    return Turn(text=text, tool_calls=list(calls), usage=Usage(tokens, 0, tokens))


def test_fixes_bug_and_verifies_independently(workspace, repo):
    provider = ScriptedProvider(
        [
            turn(call("read_file", path="calc.py")),
            turn(call("edit_file", path="calc.py", old_str="a - b", new_str="a + b")),
            turn(call("run_tests")),
            turn(text="Root cause: subtraction instead of addition. Fixed calc.py."),
        ]
    )
    result = Agent(provider, workspace).run()

    assert result.status == "fixed"
    assert result.steps == 4
    assert result.usage.total_tokens == 800
    assert "a + b" in result.diff
    assert "Root cause" in result.summary
    assert "FAILED" in provider.prompt or "failed" in provider.prompt
    assert provider.tool_results[-1][0].output.startswith("PASSED")


def test_model_claiming_success_is_not_trusted(workspace):
    provider = ScriptedProvider([turn(text="All fixed!")])
    result = Agent(provider, workspace).run()
    assert result.status == "not_fixed"


def test_already_passing_skips_the_model(workspace, repo):
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    provider = ScriptedProvider([])
    result = Agent(provider, workspace).run()
    assert result.status == "already_passing" and result.steps == 0


def test_missing_test_command_stops_before_calling_the_model(repo):
    workspace = Workspace(repo, "definitely-not-a-real-command -q")
    provider = ScriptedProvider([])
    result = Agent(provider, workspace).run()
    assert result.status == "error" and "not found" in result.error
    assert result.steps == 0 and provider.prompt == ""


def test_step_budget(workspace):
    provider = ScriptedProvider([turn(call("list_files")) for _ in range(5)])
    result = Agent(provider, workspace, Budget(max_steps=3)).run()
    assert result.status == "budget_exceeded"
    assert result.steps == 3
    assert "step limit" in result.error


def test_cost_budget(workspace):
    provider = ScriptedProvider([turn(call("list_files"), tokens=1_000_000) for _ in range(5)])
    budget = Budget(max_cost_usd=5.0)
    result = Agent(provider, workspace, budget, price=Price(4.0, 20.0)).run()
    assert result.status == "budget_exceeded"
    assert result.steps == 1  # $24 after the first step
    assert result.cost_usd == 24.0


def test_secrets_in_tool_output_are_redacted(workspace, repo):
    (repo / "settings.py").write_text('TOKEN = "ghp_' + "x" * 36 + '"\n')
    provider = ScriptedProvider([turn(call("read_file", path="settings.py")), turn(text="done")])
    Agent(provider, workspace).run()
    sent = provider.tool_results[0][0].output
    assert "ghp_" not in sent and "[REDACTED]" in sent


def test_tool_errors_are_returned_to_the_model(workspace):
    provider = ScriptedProvider([turn(call("read_file", path=".env")), turn(text="ok")])
    Agent(provider, workspace).run()
    result = provider.tool_results[0][0]
    assert result.is_error and "secret" in result.output


def test_provider_error_is_reported(workspace):
    class Failing(ScriptedProvider):
        def step(self):
            raise ProviderError("declined")

    result = Agent(Failing([]), workspace).run()
    assert result.status == "error" and result.error == "declined"


def test_trace_records_events(workspace):
    provider = ScriptedProvider([turn(call("list_files")), turn(text="done")])
    result = Agent(provider, workspace).run()
    kinds = [e["type"] for e in result.trace]
    assert kinds[0] == "initial_tests" and kinds[-1] == "final_tests"
    assert "tool_result" in kinds


def test_final_diff_is_redacted(workspace, repo):
    secret = "ghp_" + "y" * 36
    (repo / "calc.py").write_text(f'TOKEN = "{secret}"\n\ndef add(a, b):\n    return a - b\n')
    edit = call("edit_file", path="calc.py", old_str="a - b", new_str="a + b")
    result = Agent(ScriptedProvider([turn(edit), turn(text="done")]), workspace).run()
    assert "a + b" in result.diff and secret not in result.diff
