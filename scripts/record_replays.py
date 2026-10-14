"""Record real agent runs on the demo projects, for the web UI's no-key replay mode.

For each demo project: copy it to a temp dir, run the real agent with a real model,
and, only if the run ends fixed (Greenlight's own re-run of the tests passes), save the
full event log and report to src/greenlight/replays/<task>.json.

If a model's quota runs out (429/503), the next model in the list is tried.

Usage (uses your own API key, like the CLI):
    python scripts/record_replays.py                    # all demo projects
    python scripts/record_replays.py --tasks pagination --models gemini-3.5-flash-lite
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from greenlight.agent import Agent, Budget
from greenlight.keys import resolve_api_key
from greenlight.providers import create_provider
from greenlight.security import redact, register_env_secrets
from greenlight.streamlit_app import DEMO_TASKS, tasks_dir, to_report
from greenlight.tools import Workspace

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "src" / "greenlight" / "replays"
DEFAULT_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"]
INFRA_ERROR = re.compile(r"\b(429|503)\b|quota|overloaded", re.IGNORECASE)


def scrub(value, temp_dir: str):
    """Remove temp paths and anything key-shaped from recorded data."""
    if isinstance(value, str):
        return redact(value.replace(temp_dir, "<tmp>"))
    if isinstance(value, list):
        return [scrub(v, temp_dir) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v, temp_dir) for k, v in value.items()}
    return value


def record(task: str, model: str, api_key: str) -> dict | str:
    temp_dir = Path(tempfile.mkdtemp(prefix="greenlight-replay-"))
    try:
        repo = temp_dir / task
        shutil.copytree(tasks_dir() / task, repo, ignore=shutil.ignore_patterns("__pycache__"))
        provider = create_provider("gemini", api_key, model)
        workspace = Workspace(repo, f"{sys.executable} -m pytest -q -p no:cacheprovider")
        result = Agent(provider, workspace, Budget(max_steps=20)).run()
        if result.status != "fixed":
            return result.error or result.status
        info = DEMO_TASKS[task]
        report = to_report(result, f"{info.emoji} {info.title}", "gemini", provider.model)
        report.pop("test_output", None)  # long; the events already carry the final output
        payload = {
            "task": task,
            "provider": "gemini",
            "model": provider.model,
            "recorded_at": time.strftime("%Y-%m-%d"),
            "events": [
                {k: v for k, v in event.items() if k != "usage" or event["type"] == "model_turn"}
                for event in result.trace
            ],
            "report": report,
        }
        return scrub(payload, str(temp_dir.resolve()))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tasks", nargs="*", help="Demo projects (default: all)")
    parser.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    parser.add_argument("--attempts", type=int, default=2, help="Tries per model if not fixed")
    args = parser.parse_args()

    load_dotenv(find_dotenv(usecwd=True))
    register_env_secrets()
    api_key, _ = resolve_api_key("gemini")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for task in args.tasks or list(DEMO_TASKS):
        saved = False
        for model in args.models:
            for attempt in range(1, args.attempts + 1):
                print(f"→ {task} with {model} (try {attempt}) ...", end=" ", flush=True)
                outcome = record(task, model, api_key)
                if isinstance(outcome, dict):
                    out = OUT_DIR / f"{task}.json"
                    out.write_text(json.dumps(outcome, indent=1) + "\n")
                    print(f"saved ({outcome['report']['steps']} steps)")
                    saved = True
                    break
                print(f"not fixed: {outcome[:90]}")
                if INFRA_ERROR.search(outcome):
                    break  # quota/overload: move on to the next model
            if saved:
                break
        if not saved:
            print(f"   ⚠️ no replay recorded for {task}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
