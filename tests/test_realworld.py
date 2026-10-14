"""Real-world benchmark scoring and reporting, tested offline (no clones, no models)."""

import importlib.util
import sys
from pathlib import Path

REALWORLD = Path(__file__).resolve().parents[1] / "evals" / "realworld"
sys.path.insert(0, str(REALWORLD))


def load(name):
    # Registered under its plain name: run.py imports `common`, and dataclasses need the
    # module to be in sys.modules while it executes.
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REALWORLD / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


common = load("common")
run = load("run")
compare = load("compare")


def make_task(**overrides):
    fields = dict(
        id="lib-abc1234", repo="lib", url="", fix_sha="abc1234", message="Fix x", date="",
        src_files=["lib.py"], test_files=["test_lib.py"], fail_to_pass=["t::test_bug"],
        src_lines_changed=3, fails_at_fix=["t::test_env_specific"],
    )  # fmt: skip
    fields.update(overrides)
    return common.Task(**fields)


def fake_suite(monkeypatch, outcomes):
    monkeypatch.setattr(run, "run_pytest", lambda *a, **k: (1, outcomes))


def test_solved_when_fail_to_pass_passes_and_nothing_regresses(monkeypatch):
    fake_suite(
        monkeypatch,
        {"t::test_bug": "passed", "t::test_other": "passed", "t::test_env_specific": "failed"},
    )
    checks = run.score(make_task(), Path("py"), Path("."))
    assert checks["f2p_passed"] and checks["regressions"] == []


def test_regression_is_detected(monkeypatch):
    fake_suite(monkeypatch, {"t::test_bug": "passed", "t::test_other": "failed"})
    checks = run.score(make_task(), Path("py"), Path("."))
    assert checks["f2p_passed"] and checks["regressions"] == ["t::test_other"]


def test_unfixed_bug_is_detected(monkeypatch):
    fake_suite(monkeypatch, {"t::test_bug": "failed", "t::test_other": "passed"})
    checks = run.score(make_task(), Path("py"), Path("."))
    assert not checks["f2p_passed"] and checks["f2p_failing"] == ["t::test_bug"]


def test_missing_fail_to_pass_test_counts_as_failing(monkeypatch):
    fake_suite(monkeypatch, {"t::test_other": "passed"})  # e.g. the test file stopped importing
    assert not run.score(make_task(), Path("py"), Path("."))["f2p_passed"]


def test_test_command_imports_code_from_the_workspace():
    cmd = common.test_command(Path("/venv/bin/python"), make_task(src_root="src"), ["t.py"])
    assert cmd.startswith("env PYTHONPATH=src /venv/bin/python -m pytest")
    assert cmd.endswith("t.py")


def test_manifest_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "MANIFEST", tmp_path / "manifest.json")
    common.save_manifest([make_task()])
    assert common.load_manifest()[0] == make_task()


def test_comparison_report():
    def result(model, solved_flags, cost):
        tasks = [
            {"task": f"t{i}", "solved": ok, "steps": 5, "f2p_passed": ok, "regressions": []}
            for i, ok in enumerate(solved_flags)
        ]
        solved = sum(solved_flags)
        return {
            "model": model, "solved": solved, "total": len(tasks),
            "pass_rate": solved / len(tasks), "cost_per_solved_usd": cost, "avg_steps": 5,
            "total_tokens": 1000, "total_time_s": 10, "tasks": tasks,
        }  # fmt: skip

    table = compare.render(
        [result("small", [True, False], None), result("big", [True, True], 0.02)]
    )
    lines = table.splitlines()
    assert lines[2].startswith("| `big` | 2/2 | 100%")  # best model first
    assert "free tier" in table and "$0.0200" in table
    assert "| `t1` | ✅ 5 steps | ❌ |" in table


def test_manifest_tasks_are_well_formed():
    for task in common.load_manifest():
        assert task.fail_to_pass, task.id
        assert task.src_files and task.test_files, task.id
        assert not set(task.fail_to_pass) & set(task.fails_at_fix), task.id


def test_quota_failures_are_not_counted_as_attempts():
    def row(task, solved=False, status="budget_exceeded", error=""):
        return {
            "task": task, "solved": solved, "status": status, "error": error, "steps": 3,
            "f2p_passed": solved, "regressions": [], "input_tokens": 10, "output_tokens": 1,
            "cost_usd": None, "duration_s": 1.0,
        }  # fmt: skip

    rows = [
        row("a", solved=True, status="fixed"),
        row("b"),
        row("c", status="error", error="Gemini API error 429: your key hit its quota"),
    ]
    summary = run.summarize(rows, "gemini", "m")
    assert (summary["solved"], summary["attempted"], summary["not_attempted"]) == (1, 2, 1)
    assert summary["pass_rate"] == 0.5
    assert run.is_infra_failure(rows[2]) and not run.is_infra_failure(rows[1])
    assert "⚠️ not run" in compare.render([summary])
