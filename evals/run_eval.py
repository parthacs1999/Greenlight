"""Run Greenlight against every task in evals/tasks and report pass rate, cost and steps.

Each task is a small repository with a planted bug and failing tests. The task is
copied to a temp directory, so the originals are never modified. The agent only ever
sees the task's own (visible) tests.

A task counts as solved only if, after the agent finishes:
1. the visible tests pass (re-run independently by Greenlight),
2. no test file was modified, and
3. the task's hidden tests in evals/hidden/<task>/ also pass. They're copied in only
   for this final check. They exercise the documented behaviour on new inputs, so a
   fix that hard-codes the visible cases or only patches the visible symptom fails.

Every fix's diff is saved next to the results, so anyone can read exactly what the
agent changed.

Usage (uses your own API key, like the CLI):
    python evals/run_eval.py --provider claude
    python evals/run_eval.py --provider gemini --tasks pagination lru_cache
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from greenlight.agent import Agent, Budget
from greenlight.pricing import price_for
from greenlight.providers import Provider, create_provider
from greenlight.security import redact
from greenlight.tools import Workspace, is_test_file

EVALS_DIR = Path(__file__).parent
TASKS_DIR = EVALS_DIR / "tasks"
HIDDEN_DIR = EVALS_DIR / "hidden"
RESULTS_DIR = EVALS_DIR / "results"

# difficulty, what makes it tricky (shown in the report; never shown to the agent)
TASKS: dict[str, tuple[str, str]] = {
    "pagination": ("easy", "off-by-one offset, floor vs ceil"),
    "mutable_default": ("easy", "shared mutable default argument"),
    "retry_backoff": ("easy", "exponent off by one, cap ignored"),
    "cross_module_discount": ("medium", "test fails in cart.py, bug is in pricing.py"),
    "csv_quoted_fields": ("medium", "quoted fields and escaped quotes"),
    "lru_cache": ("hard", "two recency bugs; hidden tests check exact ordering"),
    "interval_merge": ("hard", "missing sort, boundary bug, must not mutate input"),
    "inventory_service": ("hard", "3 files: lost update across layers + non-atomic batch"),
    "split_bill": ("hard", "cents must sum exactly; property-based hidden tests"),
    "config_parser": ("hard", "five separate bugs in one function"),
}

ProviderFactory = Callable[[str], Provider]


def test_command() -> str:
    return f"{sys.executable} -m pytest -q -p no:cacheprovider"


def run_task(task: Path, make_provider: ProviderFactory, budget: Budget) -> dict[str, Any]:
    """Run the agent on one task and score it. Never touches the original task folder."""
    with tempfile.TemporaryDirectory(prefix=f"greenlight-{task.name}-") as tmp:
        repo = Path(tmp) / task.name
        shutil.copytree(task, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        provider = make_provider(task.name)
        workspace = Workspace(repo, test_command())
        result = Agent(provider, workspace, budget, price=price_for(provider.model)).run()

        tests_touched = sorted(rel for rel in workspace.originals if is_test_file(rel))
        visible_passed = result.status == "fixed"
        hidden_passed, hidden_output = run_hidden_tests(task.name, repo, workspace)
        difficulty, _ = TASKS.get(task.name, ("unrated", ""))
        return {
            "task": task.name,
            "difficulty": difficulty,
            "solved": visible_passed and hidden_passed and not tests_touched,
            "visible_passed": visible_passed,
            "hidden_passed": hidden_passed,
            "tests_touched": tests_touched,
            "status": result.status,
            "steps": result.steps,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cost_usd": result.cost_usd,
            "duration_s": round(result.duration_s, 1),
            "files_changed": sorted(workspace.originals),
            "diff": result.diff,
            "summary": result.summary,
            "hidden_output": "" if hidden_passed else hidden_output[-2000:],
            "error": result.error,
        }


def run_hidden_tests(task: str, repo: Path, workspace: Workspace) -> tuple[bool, str]:
    hidden = sorted((HIDDEN_DIR / task).glob("test_*.py"))
    if not hidden:
        return True, ""
    for test_file in hidden:
        shutil.copy(test_file, repo / test_file.name)
    code, output = workspace.run_test_command()
    return code == 0, redact(output)


def summarize(rows: list[dict[str, Any]], provider: str, model: str) -> dict[str, Any]:
    solved = sum(r["solved"] for r in rows)
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    by_difficulty: dict[str, dict[str, int]] = {}
    for r in rows:
        bucket = by_difficulty.setdefault(r["difficulty"], {"solved": 0, "total": 0})
        bucket["total"] += 1
        bucket["solved"] += int(r["solved"])
    return {
        "provider": provider,
        "model": model,
        "date": time.strftime("%Y-%m-%d"),
        "solved": solved,
        "total": len(rows),
        "pass_rate": round(solved / len(rows), 3) if rows else 0,
        "caught_by_hidden_tests": sum(r["visible_passed"] and not r["hidden_passed"] for r in rows),
        "by_difficulty": by_difficulty,
        "total_cost_usd": round(sum(costs), 4) if costs else None,
        "avg_steps": round(sum(r["steps"] for r in rows) / len(rows), 1) if rows else 0,
        "tasks": rows,
    }


def write_results(summary: dict[str, Any]) -> Path:
    name = f"{summary['provider']}-{summary['model']}"
    diff_dir = RESULTS_DIR / name
    if diff_dir.exists():
        shutil.rmtree(diff_dir)
    diff_dir.mkdir(parents=True)
    for row in summary["tasks"]:
        (diff_dir / f"{row['task']}.diff").write_text(row["diff"] or "# no changes\n")
    out = RESULTS_DIR / f"{name}.json"
    out.write_text(json.dumps(summary, indent=2) + "\n")
    return out


def main() -> int:
    from dotenv import find_dotenv, load_dotenv

    from greenlight.keys import resolve_api_key
    from greenlight.security import register_env_secrets

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=["claude", "gemini"], default="claude")
    parser.add_argument("--model")
    parser.add_argument("--tasks", nargs="*", help="Task names (default: all)")
    parser.add_argument("--max-steps", type=int, default=25)
    parser.add_argument("--max-cost", type=float, default=0.50, help="Per-task cost cap in USD")
    args = parser.parse_args()

    load_dotenv(find_dotenv(usecwd=True))
    register_env_secrets()
    api_key, _ = resolve_api_key(args.provider)

    tasks = sorted(p for p in TASKS_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))
    if args.tasks:
        tasks = [t for t in tasks if t.name in args.tasks]
    tasks.sort(key=lambda t: (list(TASKS).index(t.name) if t.name in TASKS else 99, t.name))
    budget = Budget(max_steps=args.max_steps, max_cost_usd=args.max_cost)

    def make_provider(_task: str) -> Provider:
        return create_provider(args.provider, api_key, model=args.model)

    model = make_provider("").model
    rows = []
    for task in tasks:
        print(f"→ {task.name} ...", end=" ", flush=True)
        row = run_task(task, make_provider, budget)
        rows.append(row)
        if row["solved"]:
            print("solved")
        elif row["visible_passed"] and not row["hidden_passed"]:
            print("FAILED (visible tests pass, hidden tests caught it)")
        else:
            print(f"FAILED ({row['status']})")

    summary = summarize(rows, args.provider, model)
    out = write_results(summary)

    print("\n| Task | Difficulty | Solved | Visible | Hidden | Steps | Time |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        print(
            f"| {r['task']} | {r['difficulty']} | {'✅' if r['solved'] else '❌'} | "
            f"{'pass' if r['visible_passed'] else 'fail'} | "
            f"{'pass' if r['hidden_passed'] else 'fail'} | {r['steps']} | {r['duration_s']}s |"
        )
    print(f"\n{model}: {summary['solved']}/{summary['total']} solved {summary['by_difficulty']}")
    print(f"Results: {out}  Diffs: {out.with_suffix('')}/")
    return 0 if summary["solved"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
