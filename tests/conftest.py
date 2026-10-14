from __future__ import annotations

import sys
from pathlib import Path

import pytest

from greenlight.providers.base import Provider, ToolResult, Turn
from greenlight.tools import Workspace

PYTEST_CMD = f"{sys.executable} -m pytest -q -p no:cacheprovider"

BUGGY_SOURCE = "def add(a, b):\n    return a - b\n"
TEST_SOURCE = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A tiny repository with one bug and one failing test."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calc.py").write_text(BUGGY_SOURCE)
    (root / "test_calc.py").write_text(TEST_SOURCE)
    (root / ".env").write_text("ANTHROPIC_API_KEY=" + "sk-ant-" + "fake" * 6 + "\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    return root


@pytest.fixture
def workspace(repo: Path) -> Workspace:
    return Workspace(repo, PYTEST_CMD)


class ScriptedProvider(Provider):
    """Replays a fixed list of turns; records everything the agent sends back."""

    name = "fake"
    model = "fake-model"

    def __init__(self, turns: list[Turn]) -> None:
        self.turns = list(turns)
        self.prompt = ""
        self.tool_results: list[list[ToolResult]] = []

    def start(self, system, tools, prompt):
        self.system, self.tool_names, self.prompt = system, [t.name for t in tools], prompt

    def step(self) -> Turn:
        return self.turns.pop(0)

    def add_tool_results(self, results):
        self.tool_results.append(results)
