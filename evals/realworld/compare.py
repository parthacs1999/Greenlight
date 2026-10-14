"""Compare models on the real-world benchmark: a leaderboard plus a task-by-model grid.

Reads every evals/results/realworld/<provider>-<model>.json and prints Markdown
(ready to paste into the README), also written to evals/results/realworld/COMPARISON.md.

Usage:
    python evals/realworld/compare.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).parents[1] / "results" / "realworld"
INFRA_ERROR = re.compile(r"\b(429|503)\b|RESOURCE_EXHAUSTED|overloaded|quota", re.IGNORECASE)


def is_infra(row: dict) -> bool:
    return bool(INFRA_ERROR.search(row.get("error") or ""))


def money(value: float | None) -> str:
    return "free tier" if value is None else f"${value:.4f}"


def solved_cell(run: dict) -> str:
    attempted = run.get("attempted", run["total"])
    cell = f"{run['solved']}/{attempted}"
    not_attempted = run.get("not_attempted", 0)
    return cell + (f" ({not_attempted} not run: quota)" if not_attempted else "")


def render(runs: list[dict]) -> str:
    runs = sorted(runs, key=lambda r: (-r["pass_rate"], r.get("cost_per_solved_usd") or 0))
    lines = [
        "| Model | Solved / attempted | Pass rate | Cost / solved | Avg. steps | Tokens | Time |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        lines.append(
            f"| `{r['model']}` | {solved_cell(r)} | {r['pass_rate']:.0%} | "
            f"{money(r.get('cost_per_solved_usd'))} | {r['avg_steps']} | "
            f"{r['total_tokens'] / 1000:.0f}k | {r['total_time_s']:.0f}s |"
        )

    tasks = sorted({t["task"] for r in runs for t in r["tasks"]})
    header = "| Bug (real commit) | " + " | ".join(f"`{r['model']}`" for r in runs) + " |"
    lines += ["", header, "|---|" + "---|" * len(runs)]
    by_model = [{t["task"]: t for t in r["tasks"]} for r in runs]
    for task in tasks:
        cells = []
        for results in by_model:
            row = results.get(task)
            if row is None:
                cells.append("–")
            elif row.get("status") == "error" and is_infra(row):
                cells.append("⚠️ not run")
            elif row["solved"]:
                cells.append(f"✅ {row['steps']} steps")
            elif row.get("f2p_passed") and row.get("regressions"):
                cells.append("❌ regression")
            else:
                cells.append("❌")
        lines.append(f"| `{task}` | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    runs = [json.loads(p.read_text()) for p in sorted(RESULTS_DIR.glob("*.json"))]
    if not runs:
        print("No results yet. Run evals/realworld/run.py first.")
        return 1
    table = render(runs)
    (RESULTS_DIR / "COMPARISON.md").write_text(table)
    print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
