import json

from greenlight import github_report


def write_trace(repo, **fields):
    runs = repo / ".greenlight" / "runs"
    runs.mkdir(parents=True)
    trace = {
        "provider": "gemini",
        "model": "m",
        "test_cmd": "pytest -q",
        "status": "fixed",
        "steps": 5,
        "usage": {"input_tokens": 1200, "output_tokens": 80},
        "cost_usd": None,
        "duration_s": 12.5,
        "summary": "Root cause: percent used as a fraction.",
        "error": "",
        "diff": "--- a/x.py\n+++ b/x.py\n-a\n+b\n",
        **fields,
    }
    (runs / "20260101-000000.json").write_text(json.dumps(trace))


def test_fixed_run_writes_outputs_summary_and_body(tmp_path, monkeypatch):
    out, summary = tmp_path / "out", tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    repo = tmp_path / "repo"
    write_trace(repo)

    github_report.main([str(repo), "0"])

    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert outputs["status"] == "fixed" and outputs["fixed"] == "true" and outputs["steps"] == "5"
    body = (repo / ".greenlight" / "pr-body.md").read_text()
    assert "fixed the failing tests" in body and "```diff" in body and "+b" in body
    assert summary.read_text().strip() == body.strip()
    # The body lives in the self-ignoring folder, so a PR step never commits it.
    assert (repo / ".greenlight" / ".gitignore").read_text() == "*\n"


def test_no_trace_means_already_passing_or_error(tmp_path):
    assert github_report.build_report(None, 0)["status"] == "already_passing"
    assert github_report.build_report(None, 2)["status"] == "error"


def test_fence_cannot_be_broken_out_of():
    diff = "+ x = '```'\n+ y = '````'\n"
    report = github_report.build_report(
        {"status": "fixed", "diff": diff, "summary": "", "usage": {}}, 0
    )
    assert "`````diff" in report["body"]


def test_secrets_are_redacted_from_the_body():
    leaked = "ghp_" + "z" * 36
    report = github_report.build_report(
        {"status": "not_fixed", "summary": f"found {leaked}", "usage": {}}, 1
    )
    assert leaked not in report["body"]
