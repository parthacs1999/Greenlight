"""Turn the latest Greenlight run into GitHub Actions outputs, a job summary and a PR body.

Used by action.yml after `greenlight fix` exits:

    python -m greenlight.github_report <repo> <exit-code>

Writes `status`, `fixed`, `steps` and `body-path` to $GITHUB_OUTPUT, a Markdown report to
$GITHUB_STEP_SUMMARY, and the pull-request description to <repo>/.greenlight/pr-body.md
(inside the self-ignoring .greenlight/ folder, so it is never committed).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .security import redact

TITLES = {
    "fixed": "✅ Greenlight fixed the failing tests",
    "already_passing": "✅ Tests already pass, nothing to fix",
    "not_fixed": "❌ Greenlight couldn't fix the failing tests",
    "budget_exceeded": "⏸️ Greenlight stopped at its budget",
    "error": "⚠️ Greenlight run failed",
}


def latest_trace(repo: Path) -> dict[str, Any] | None:
    runs = sorted((repo / ".greenlight" / "runs").glob("*.json"))
    return json.loads(runs[-1].read_text()) if runs else None


def fence(text: str) -> str:
    """A code fence longer than any backtick run in `text`, so it can't be broken out of."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def build_report(trace: dict[str, Any] | None, exit_code: int) -> dict[str, Any]:
    if trace is None:
        # The CLI saves no trace when the tests already pass, or when it stops before running.
        status = "already_passing" if exit_code == 0 else "error"
        return {"status": status, "steps": 0, "body": TITLES[status] + "\n"}

    status = trace.get("status", "error")
    usage = trace.get("usage") or {}
    cost = trace.get("cost_usd")
    lines = [
        f"## {TITLES.get(status, status)}",
        "",
        "| | |",
        "|---|---|",
        f"| Model | `{trace.get('provider', '?')} / {trace.get('model', '?')}` |",
        f"| Verified by | re-running `{trace.get('test_cmd', '?')}` independently |",
        f"| Steps | {trace.get('steps', 0)} |",
        f"| Tokens | {usage.get('input_tokens', 0):,} in / {usage.get('output_tokens', 0):,} out |",
        f"| Est. cost | {'n/a' if cost is None else f'${cost:.4f}'} |",
        f"| Time | {trace.get('duration_s', 0)}s |",
    ]
    if trace.get("error"):
        lines += ["", f"**Reason:** {trace['error']}"]
    if trace.get("summary"):
        summary = trace["summary"].strip()
        lines += [
            "",
            "### Agent's explanation",
            "",
            f"{fence(summary)}text",
            summary,
            fence(summary),
        ]
    if trace.get("diff"):
        diff = trace["diff"]
        lines += ["", "### Changes", "", f"{fence(diff)}diff", diff.rstrip("\n"), fence(diff)]
    lines += [
        "",
        "---",
        "*Opened by [Greenlight](https://github.com/parthacs1999/coding-agent). Please review "
        "the change before merging: tests passing is necessary, not sufficient.*",
    ]
    return {"status": status, "steps": trace.get("steps", 0), "body": redact("\n".join(lines))}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    repo = Path(args[0] if args else ".")
    exit_code = int(args[1]) if len(args) > 1 else 0

    report = build_report(latest_trace(repo), exit_code)
    body_path = repo / ".greenlight" / "pr-body.md"
    body_path.parent.mkdir(parents=True, exist_ok=True)
    (repo / ".greenlight" / ".gitignore").write_text("*\n")
    body_path.write_text(report["body"])

    outputs = {
        "status": report["status"],
        "fixed": str(report["status"] == "fixed").lower(),
        "steps": str(report["steps"]),
        "body-path": str(body_path.resolve()),
    }
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a") as fh:
            fh.writelines(f"{k}={v}\n" for k, v in outputs.items())
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as fh:
            fh.write(report["body"] + "\n")
    print(f"greenlight status: {report['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
