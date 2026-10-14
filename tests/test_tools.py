from greenlight.tools import Workspace, is_test_file, truncate

from .conftest import PYTEST_CMD


def test_read_file_numbers_lines(workspace):
    out, is_error = workspace.execute("read_file", {"path": "calc.py"})
    assert not is_error
    assert "    1  def add(a, b):" in out
    assert "    2      return a - b" in out


def test_read_file_line_range(workspace):
    out, _ = workspace.execute("read_file", {"path": "calc.py", "start_line": 2, "end_line": 2})
    assert out.strip().startswith("2")
    assert "def add" not in out


def test_read_secret_file_is_refused(workspace):
    out, is_error = workspace.execute("read_file", {"path": ".env"})
    assert is_error
    assert "sk-ant-" not in out


def test_list_files_hides_secrets_and_git(workspace):
    out, _ = workspace.execute("list_files", {})
    assert "calc.py" in out and "test_calc.py" in out
    assert ".env" not in out and ".git" not in out


def test_search_finds_matches_and_skips_secrets(workspace):
    out, _ = workspace.execute("search", {"pattern": r"return|API_KEY"})
    assert "calc.py:2:" in out
    assert ".env" not in out


def test_search_invalid_regex(workspace):
    out, is_error = workspace.execute("search", {"pattern": "("})
    assert is_error and "invalid regex" in out


def test_edit_file_replaces_unique_match(workspace, repo):
    out, is_error = workspace.execute(
        "edit_file", {"path": "calc.py", "old_str": "a - b", "new_str": "a + b"}
    )
    assert not is_error, out
    assert "a + b" in (repo / "calc.py").read_text()
    assert "-    return a - b" in workspace.diff()
    assert "+    return a + b" in workspace.diff()


def test_edit_file_not_found_and_ambiguous(workspace, repo):
    out, is_error = workspace.execute(
        "edit_file", {"path": "calc.py", "old_str": "nope", "new_str": "x"}
    )
    assert is_error and "not found" in out
    (repo / "dup.py").write_text("x = 1\nx = 1\n")
    out, is_error = workspace.execute(
        "edit_file", {"path": "dup.py", "old_str": "x = 1", "new_str": "x = 2"}
    )
    assert is_error and "2 places" in out


def test_test_files_are_read_only_by_default(workspace, repo):
    out, is_error = workspace.execute(
        "edit_file", {"path": "test_calc.py", "old_str": "== 5", "new_str": "== -1"}
    )
    assert is_error and "read-only" in out
    assert "== 5" in (repo / "test_calc.py").read_text()


def test_test_edits_can_be_allowed(repo):
    ws = Workspace(repo, PYTEST_CMD, allow_test_edits=True)
    _, is_error = ws.execute(
        "edit_file", {"path": "test_calc.py", "old_str": "== 5", "new_str": "== 5  # ok"}
    )
    assert not is_error


def test_write_file_creates_but_never_overwrites(workspace, repo):
    _, is_error = workspace.execute("write_file", {"path": "pkg/new.py", "content": "X = 1\n"})
    assert not is_error and (repo / "pkg" / "new.py").exists()
    out, is_error = workspace.execute("write_file", {"path": "calc.py", "content": ""})
    assert is_error and "already exists" in out


def test_review_rejection_blocks_write(repo):
    ws = Workspace(repo, PYTEST_CMD, approve=lambda rel, diff: False)
    out, is_error = ws.execute("edit_file", {"path": "calc.py", "old_str": "-", "new_str": "+"})
    assert is_error and "rejected" in out
    assert "a - b" in (repo / "calc.py").read_text()


def test_run_tests_reports_failure_then_success(workspace, repo):
    out, _ = workspace.execute("run_tests", {})
    assert out.startswith("FAILED")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    out, _ = workspace.execute("run_tests", {"target": "test_calc.py::test_add"})
    assert out.startswith("PASSED")


def test_run_tests_rejects_flag_injection(workspace):
    for target in ["-k foo", "--co", "test_calc.py --pdb", "../elsewhere.py"]:
        out, is_error = workspace.execute("run_tests", {"target": target})
        assert is_error, target


def test_tests_cannot_see_api_keys(repo, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "fake" * 6)
    (repo / "test_env.py").write_text(
        "import os\n\ndef test_env():\n    assert 'ANTHROPIC_API_KEY' not in os.environ\n"
    )
    code, out = Workspace(repo, PYTEST_CMD).run_test_command("test_env.py")
    assert code == 0, out


def test_unknown_tool_and_bad_args(workspace):
    assert workspace.execute("rm_rf", {})[1]
    out, is_error = workspace.execute("read_file", {"path": "calc.py", "mode": "w"})
    assert is_error and "Unexpected" in out
    out, is_error = workspace.execute("edit_file", {"path": "calc.py"})
    assert is_error and "Missing" in out


def test_is_test_file():
    assert is_test_file("test_x.py") and is_test_file("pkg/x_test.py")
    assert is_test_file("tests/helpers.py") and is_test_file("conftest.py")
    assert not is_test_file("src/testing_utils.py") and not is_test_file("calc.py")


def test_truncate_keeps_tail():
    out = truncate("a" * 50 + "END", limit=10)
    assert out.endswith("END") and "truncated" in out
