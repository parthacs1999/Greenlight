"""Run Greenlight against every task in evals/tasks and report pass rate, cost and steps.

Each task is a small repository with a planted bug and failing tests. The task is
copied to a temp directory, so the originals are never modified. A task counts as
solved only if the test command passes afterwards AND no test file was changed.

Usage (uses your own API key, like the CLI):
    python evals/run_eval.py --provider claude
    python evals/run_eval.py --provider gemini --tasks pagination
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from greenlight.agent import Agent, Budget
from greenlight.keys import resolve_api_key
from greenlight.pricing import price_for
from greenlight.providers import create_provider
from greenlight.security import register_env_secrets
from greenlight.tools import Workspace, is_test_file

TASKS_DIR = Path(__file__).parent / "tasks"
RESULTS_DIR = Path(__file__).parent / "results"


def run_task(task: Path, provider_name: str, model: str | None, api_key: str, budget: Budget):
    with tempfile.TemporaryDirectory(prefix=f"greenlight-{task.name}-") as tmp:
        repo = Path(tmp) / task.name
        shutil.copytree(task, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        provider = create_provider(provider_name, api_key, model=model)
        workspace = Workspace(repo, f"{sys.executable} -m pytest -q -p no:cacheprovider")
        result = Agent(provider, workspace, budget, price=price_for(provider.model)).run()
        tests_touched = [rel for rel in workspace.originals if is_test_file(rel)]
        return {
            "task": task.name,
            "solved": result.status == "fixed" and not tests_touched,
            "status": result.status,
            "steps": result.steps,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cost_usd": result.cost_usd,
            "duration_s": round(result.duration_s, 1),
            "files_changed": sorted(workspace.originals),
            "error": result.error,
        }, provider.model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=["claude", "gemini"], default="claude")
    parser.add_argument("--model")
    parser.add_argument("--tasks", nargs="*", help="Task names (default: all)")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--max-cost", type=float, default=0.50, help="Per-task cost cap in USD")
    args = parser.parse_args()

    load_dotenv(find_dotenv(usecwd=True))
    register_env_secrets()
    api_key, _ = resolve_api_key(args.provider)

    tasks = sorted(p for p in TASKS_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))
    if args.tasks:
        tasks = [t for t in tasks if t.name in args.tasks]
    budget = Budget(max_steps=args.max_steps, max_cost_usd=args.max_cost)

    rows, model = [], args.model
    for task in tasks:
        print(f"→ {task.name} ...", end=" ", flush=True)
        row, model = run_task(task, args.provider, args.model, api_key, budget)
        rows.append(row)
        print("solved" if row["solved"] else f"FAILED ({row['status']})")

    solved = sum(r["solved"] for r in rows)
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    summary = {
        "provider": args.provider,
        "model": model,
        "date": time.strftime("%Y-%m-%d"),
        "solved": solved,
        "total": len(rows),
        "pass_rate": round(solved / len(rows), 3) if rows else 0,
        "total_cost_usd": round(sum(costs), 4) if costs else None,
        "avg_steps": round(sum(r["steps"] for r in rows) / len(rows), 1) if rows else 0,
        "tasks": rows,
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{args.provider}-{model}.json"
    out.write_text(json.dumps(summary, indent=2))

    print("\n| Task | Solved | Steps | Tokens (in/out) | Cost | Time |\n|---|---|---|---|---|---|")
    for r in rows:
        cost = f"${r['cost_usd']:.4f}" if r["cost_usd"] is not None else "n/a"
        print(
            f"| {r['task']} | {'✅' if r['solved'] else '❌'} | {r['steps']} | "
            f"{r['input_tokens']:,}/{r['output_tokens']:,} | {cost} | {r['duration_s']}s |"
        )
    print(f"\n{model}: {solved}/{len(rows)} solved. Results written to {out}")
    return 0 if solved == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
