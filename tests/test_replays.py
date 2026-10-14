"""Every recorded replay must show a genuine fix: its diff passes the task's hidden tests."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPLAYS = sorted((ROOT / "src" / "greenlight" / "replays").glob("*.json"))


@pytest.mark.parametrize("path", REPLAYS, ids=lambda p: p.stem)
def test_replay_fix_passes_visible_and_hidden_tests(path, tmp_path):
    replay = json.loads(path.read_text())
    repo = tmp_path / replay["task"]
    shutil.copytree(ROOT / "evals" / "tasks" / replay["task"], repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "apply", "-"], cwd=repo, input=replay["report"]["diff"], text=True, check=True
    )
    for hidden in (ROOT / "evals" / "hidden" / replay["task"]).glob("test_*.py"):
        shutil.copy(hidden, repo / hidden.name)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stdout[-1500:]
