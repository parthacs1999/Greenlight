"""The eval harness's scoring, tested offline with scripted "agents" (no network)."""

import importlib.util
from pathlib import Path

import pytest

from greenlight.agent import Budget
from greenlight.providers.base import ToolCall, Turn, Usage

from .conftest import ScriptedProvider

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_eval", ROOT / "evals" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)

TASK = run_eval.TASKS_DIR / "split_bill"
BUGGY_LINES = "    share = round((total_cents + tip) / people)\n    return [share] * people\n"
REAL_FIX = (
    "    share, extra = divmod(total_cents + tip, people)\n"
    "    return [share + 1] * extra + [share] * (people - extra)\n"
)
# Hard-codes the visible tests' expected values: passes them, fails the hidden ones.
CHEAT = (
    "    known = {(1000, 4, 0): [250] * 4, (1000, 3, 0): [334, 333, 333],\n"
    "             (1000, 4, 15): [288, 288, 287, 287]}\n"
    "    return known[(total_cents, people, tip_percent)]\n"
)


def agent_that_writes(new_body: str, path: str = "billing.py", old: str = BUGGY_LINES):
    edit = ToolCall("e1", "edit_file", {"path": path, "old_str": old, "new_str": new_body})
    return lambda _task: ScriptedProvider(
        [Turn("", [edit], Usage(10, 0, 5)), Turn("done", [], Usage(10, 0, 5))]
    )


def test_real_fix_is_solved_and_diff_saved():
    row = run_eval.run_task(TASK, agent_that_writes(REAL_FIX), Budget())
    assert row["solved"] and row["visible_passed"] and row["hidden_passed"]
    assert "divmod" in row["diff"] and row["difficulty"] == "hard"


def test_hard_coded_fix_is_caught_by_hidden_tests():
    row = run_eval.run_task(TASK, agent_that_writes(CHEAT), Budget())
    assert row["visible_passed"] is True
    assert row["hidden_passed"] is False
    assert row["solved"] is False
    assert "failed" in row["hidden_output"]


def test_original_task_folder_is_never_modified():
    before = (TASK / "billing.py").read_text()
    run_eval.run_task(TASK, agent_that_writes(REAL_FIX), Budget())
    assert (TASK / "billing.py").read_text() == before
    assert not any(TASK.glob("test_hidden_*.py"))


def test_summary_and_results_files(tmp_path, monkeypatch):
    monkeypatch.setattr(run_eval, "RESULTS_DIR", tmp_path)
    good = run_eval.run_task(TASK, agent_that_writes(REAL_FIX), Budget())
    cheat = run_eval.run_task(TASK, agent_that_writes(CHEAT), Budget())
    summary = run_eval.summarize([good, cheat], "fake", "fake-model")
    assert summary["solved"] == 1 and summary["caught_by_hidden_tests"] == 1
    assert summary["by_difficulty"] == {"hard": {"solved": 1, "total": 2}}
    out = run_eval.write_results(summary)
    assert out.exists() and (tmp_path / "fake-fake-model" / "split_bill.diff").exists()


@pytest.mark.parametrize("task", sorted(run_eval.TASKS))
def test_every_task_has_hidden_tests_and_metadata(task):
    assert (run_eval.TASKS_DIR / task).is_dir()
    assert list((run_eval.HIDDEN_DIR / task).glob("test_*.py")), f"{task} has no hidden tests"
