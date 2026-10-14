"""Run Greenlight on the real-world benchmark (real bug-fix commits, see common.py).

Solved = the fix's FAIL_TO_PASS tests pass, no PASS_TO_PASS test regresses (the whole
project suite is re-run), and no test file was modified.

Usage (uses your own API key):
    python evals/realworld/run.py --provider gemini --model gemini-3.1-flash-lite
    python evals/realworld/run.py --provider claude --model claude-sonnet-5-5 --tasks toolz-d2eba03
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from common import CACHE, Task, clone, ensure_env, load_manifest, materialize, run_pytest  # noqa: E402
from common import test_command as make_test_command  # noqa: E402
from mine import REPOS  # noqa: E402

from greenlight.agent import Agent, Budget  # noqa: E402
from greenlight.pricing import price_for  # noqa: E402
from greenlight.providers import Provider, create_provider  # noqa: E402
from greenlight.tools import Workspace, is_test_file  # noqa: E402

RESULTS_DIR = Path(__file__).parents[1] / "results" / "realworld"


def python_for(task: Task) -> Path:
    repo = next(r for r in REPOS if r.name == task.repo)
    return ensure_env(repo, clone(task.url, task.repo))


def score(task: Task, python: Path, workspace: Path) -> dict[str, Any]:
    """Re-run the project's whole suite in the agent's workspace."""
    _, outcomes = run_pytest(python, workspace, task.src_root, task.test_paths)
    f2p_failing = [t for t in task.fail_to_pass if outcomes.get(t) != "passed"]
    ignored = set(task.fails_at_fix)
    regressions = sorted(
        t
        for t, o in outcomes.items()
        if o == "failed" and t not in ignored and t not in task.fail_to_pass
    )
    return {
        "f2p_passed": not f2p_failing,
        "f2p_failing": f2p_failing,
        "regressions": regressions[:20],
        "suite_size": len(outcomes),
    }


def run_task(task: Task, make_provider, budget: Budget) -> dict[str, Any]:
    python = python_for(task)
    temp_dir, ws = materialize(task, buggy=True)
    try:
        provider: Provider = make_provider()
        workspace = Workspace(ws, make_test_command(python, task, task.test_files))
        result = Agent(provider, workspace, budget, price=price_for(provider.model)).run()
        tests_touched = sorted(rel for rel in workspace.originals if is_test_file(rel))
        checks = score(task, python, ws)
        solved = checks["f2p_passed"] and not checks["regressions"] and not tests_touched
        return {
            "task": task.id,
            "repo": task.repo,
            "commit": task.fix_sha,
            "message": task.message,
            "solved": solved,
            **checks,
            "tests_touched": tests_touched,
            "status": result.status,
            "steps": result.steps,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cost_usd": result.cost_usd,
            "duration_s": round(result.duration_s, 1),
            "diff": result.diff,
            "summary": result.summary,
            "error": result.error,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


INFRA_ERROR = re.compile(r"\b(429|503)\b|RESOURCE_EXHAUSTED|overloaded|quota", re.IGNORECASE)


def is_infra_failure(row: dict[str, Any]) -> bool:
    """The provider refused to run (quota/overload): not a verdict on the model."""
    return row["status"] == "error" and bool(INFRA_ERROR.search(row.get("error") or ""))


def summarize(rows: list[dict[str, Any]], provider: str, model: str) -> dict[str, Any]:
    attempted = [r for r in rows if not is_infra_failure(r)]
    solved = sum(r["solved"] for r in attempted)
    costs = [r["cost_usd"] for r in attempted if r["cost_usd"] is not None]
    outcomes = {
        "solved": solved,
        "regression": sum(r["f2p_passed"] and bool(r["regressions"]) for r in attempted),
        "out_of_steps": sum(r["status"] == "budget_exceeded" for r in attempted),
    }
    outcomes["other_failure"] = len(attempted) - sum(outcomes.values())
    return {
        "suite": "realworld",
        "provider": provider,
        "model": model,
        "date": time.strftime("%Y-%m-%d"),
        "solved": solved,
        "attempted": len(attempted),
        "not_attempted": len(rows) - len(attempted),
        "total": len(rows),
        "pass_rate": round(solved / len(attempted), 3) if attempted else 0,
        "outcomes": outcomes,
        "total_cost_usd": round(sum(costs), 4) if costs else None,
        "cost_per_solved_usd": round(sum(costs) / solved, 4) if costs and solved else None,
        "avg_steps": round(sum(r["steps"] for r in attempted) / len(attempted), 1)
        if attempted
        else 0,
        "total_tokens": sum(r["input_tokens"] + r["output_tokens"] for r in attempted),
        "total_time_s": round(sum(r["duration_s"] for r in attempted), 1),
        "tasks": rows,
    }


def oracle_check(task: Task) -> tuple[bool, bool]:
    """(real fix scores as solved, untouched bug scores as not solved). No model involved."""
    python = python_for(task)
    results = []
    for buggy in (False, True):
        temp_dir, ws = materialize(task, buggy=buggy)
        try:
            checks = score(task, python, ws)
            results.append(checks["f2p_passed"] and not checks["regressions"])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    return results[0], not results[1]


def main() -> int:
    if "--oracle" in sys.argv:
        tasks = load_manifest()
        good = 0
        for task in tasks:
            fix_solves, bug_fails = oracle_check(task)
            good += fix_solves and bug_fails
            print(f"{'OK  ' if fix_solves and bug_fails else 'FAIL'} {task.id}: "
                  f"real fix solves={fix_solves}, bug detected={bug_fails}")  # fmt: skip
        print(f"\nOracle: {good}/{len(tasks)} tasks score correctly")
        return 0 if good == len(tasks) else 1

    from dotenv import find_dotenv, load_dotenv

    from greenlight.keys import resolve_api_key
    from greenlight.security import register_env_secrets

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=["claude", "gemini"], default="gemini")
    parser.add_argument("--model")
    parser.add_argument("--tasks", nargs="*")
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--max-cost", type=float, default=1.0)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Keep earlier results; only run tasks that are missing or hit quota/overload",
    )
    parser.add_argument(
        "--rescore", action="store_true", help="Only rebuild the summary from saved results"
    )
    args = parser.parse_args()

    load_dotenv(find_dotenv(usecwd=True))
    register_env_secrets()
    api_key, _ = resolve_api_key(args.provider)

    def make_provider() -> Provider:
        return create_provider(args.provider, api_key, model=args.model)

    model = make_provider().model
    out = RESULTS_DIR / f"{args.provider}-{model}.json"
    previous = {}
    if (args.resume or args.rescore) and out.exists():
        previous = {r["task"]: r for r in json.loads(out.read_text())["tasks"]}

    tasks = [t for t in load_manifest() if not args.tasks or t.id in args.tasks]
    todo = (
        []
        if args.rescore
        else [t for t in tasks if t.id not in previous or is_infra_failure(previous[t.id])]
    )
    budget = Budget(max_steps=args.max_steps, max_cost_usd=args.max_cost)
    print(f"{len(todo)}/{len(tasks)} real-world tasks to run · {args.provider}/{model} · {CACHE}")

    rows = dict(previous)
    for task in todo:
        print(f"→ {task.id} ({task.message[:50]}) ...", end=" ", flush=True)
        row = run_task(task, make_provider, budget)
        rows[task.id] = row
        if row["solved"]:
            print(f"solved in {row['steps']} steps")
        elif is_infra_failure(row):
            print("NOT ATTEMPTED (quota/overload)")
        elif row["f2p_passed"] and row["regressions"]:
            print(f"FAILED: {len(row['regressions'])} regression(s)")
        else:
            print(f"FAILED ({row['status']})")

    ordered = [rows[t.id] for t in tasks if t.id in rows]
    summary = summarize(ordered, args.provider, model)
    diff_dir = RESULTS_DIR / out.stem
    shutil.rmtree(diff_dir, ignore_errors=True)
    diff_dir.mkdir(parents=True)
    for row in ordered:
        (diff_dir / f"{row['task']}.diff").write_text(row["diff"] or "# no changes\n")
    out.write_text(json.dumps(summary, indent=2) + "\n")
    print(
        f"\n{model}: {summary['solved']}/{summary['attempted']} attempted real bugs fixed "
        f"({summary['not_attempted']} not attempted: quota/overload). Results: {out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
