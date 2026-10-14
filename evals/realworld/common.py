"""Shared helpers for the real-world benchmark: repos, environments, task workspaces.

A real-world task is a real bug-fix commit from an open-source project, reproduced the
way SWE-bench does it:

- The workspace is the project at the fix commit with only the *source* part of the fix
  reverted. That brings back the original bug, while the tests the developers added with
  the fix stay in place.
- Visible tests: the test files touched by the fix (the agent sees and runs these).
- FAIL_TO_PASS: tests in those files that fail on the bug and pass with the real fix.
- PASS_TO_PASS: every test of the full suite that passes with the real fix. Tests that
  already fail at the fix commit in this environment (e.g. ones needing installed package
  metadata) are recorded in `fails_at_fix` and ignored, as SWE-bench does.
- Solved = FAIL_TO_PASS pass AND no PASS_TO_PASS test fails (no regressions) AND no test
  file was modified.

Clones and per-project virtualenvs are cached in ~/.cache/greenlight-evals. Each venv holds
the project's *dependencies only* (the project itself is uninstalled), so tests import the
code from the agent's working copy, never from site-packages.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path

CACHE = Path(os.environ.get("GREENLIGHT_EVAL_CACHE", Path.home() / ".cache" / "greenlight-evals"))
MANIFEST = Path(__file__).parent / "manifest.json"
PYTEST_BASE = ["-q", "-p", "no:cacheprovider", "-o", "addopts=", "-W", "ignore"]


@dataclass(frozen=True)
class Repo:
    name: str
    url: str
    package: str  # distribution name to uninstall after installing deps
    src_root: str = "."  # where the importable package lives ("src" for src-layout)
    deps: tuple[str, ...] = ("pytest",)
    test_paths: tuple[str, ...] = ()  # full-suite targets; empty = pytest's default discovery


@dataclass
class Task:
    id: str
    repo: str
    url: str
    fix_sha: str
    message: str
    date: str
    src_files: list[str]
    test_files: list[str]
    fail_to_pass: list[str]
    src_lines_changed: int
    src_root: str = "."
    test_paths: list[str] = field(default_factory=list)
    fails_at_fix: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        return cls(**data)


def git(repo: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    ).stdout


def clone(url: str, name: str) -> Path:
    dest = CACHE / "repos" / name
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", "--filter=blob:none", url, str(dest)], check=True
        )
    return dest


def ensure_env(repo: Repo, clone_dir: Path) -> Path:
    """A venv with the project's dependencies (not the project itself). Returns its python."""
    venv = CACHE / "venvs" / repo.name
    python = venv / "bin" / "python"
    marker = venv / ".ready"
    if marker.exists():
        return python
    shutil.rmtree(venv, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    pip = [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    subprocess.run([*pip, str(clone_dir)], check=True, capture_output=True)
    subprocess.run([*pip, *repo.deps], check=True, capture_output=True)
    subprocess.run(
        [str(python), "-m", "pip", "uninstall", "-y", "--quiet", repo.package],
        check=True,
        capture_output=True,
    )
    marker.write_text("ok\n")
    return python


def export_tree(clone_dir: Path, sha: str, dest: Path) -> None:
    """Write the project's files at `sha` into `dest`, without git history."""
    archive = subprocess.run(
        ["git", "-C", str(clone_dir), "archive", "--format=tar", sha],
        capture_output=True,
        check=True,
    ).stdout
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")


def revert_source(clone_dir: Path, task: Task, dest: Path) -> None:
    """Put the pre-fix version of the fix's source files into `dest` (brings the bug back)."""
    for rel in task.src_files:
        old = subprocess.run(
            ["git", "-C", str(clone_dir), "show", f"{task.fix_sha}^:{rel}"],
            capture_output=True,
        )
        target = dest / rel
        if old.returncode == 0:
            target.write_bytes(old.stdout)
        elif target.exists():  # the file was added by the fix
            target.unlink()


def materialize(task: Task, *, buggy: bool = True) -> tuple[Path, Path]:
    """Create a temp workspace for a task. Returns (temp_dir, workspace_dir)."""
    clone_dir = CACHE / "repos" / task.repo
    temp_dir = Path(tempfile.mkdtemp(prefix=f"greenlight-rw-{task.id}-"))
    workspace = temp_dir / task.repo
    try:
        export_tree(clone_dir, task.fix_sha, workspace)
        if buggy:
            revert_source(clone_dir, task, workspace)
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return temp_dir, workspace


def test_command(python: Path, task_or_root: Task | str, targets: list[str]) -> str:
    """A shell-free test command: `env PYTHONPATH=<src_root> python -m pytest ...`."""
    src_root = task_or_root.src_root if isinstance(task_or_root, Task) else task_or_root
    parts = ["env", f"PYTHONPATH={src_root}", str(python), "-m", "pytest", *PYTEST_BASE]
    return " ".join([*parts, *targets])


def run_pytest(
    python: Path, cwd: Path, src_root: str, targets: list[str], timeout: int = 300
) -> tuple[int, dict[str, str]]:
    """Run pytest and return (exit code, {test id: outcome})."""
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as fh:
        report = Path(fh.name)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": src_root,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        proc = subprocess.run(
            [str(python), "-m", "pytest", *PYTEST_BASE, f"--junitxml={report}", *targets],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        code = proc.returncode
    except subprocess.TimeoutExpired:
        return 124, {}
    outcomes: dict[str, str] = {}
    try:
        for case in ET.parse(report).iter("testcase"):
            test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
            kinds = {child.tag for child in case}
            outcomes[test_id] = (
                "failed" if kinds & {"failure", "error"} else
                "skipped" if "skipped" in kinds else "passed"
            )  # fmt: skip
    except (ET.ParseError, FileNotFoundError):
        pass
    finally:
        report.unlink(missing_ok=True)
    return code, outcomes


def load_manifest() -> list[Task]:
    if not MANIFEST.exists():
        return []
    return [Task.from_dict(t) for t in json.loads(MANIFEST.read_text())["tasks"]]


def save_manifest(tasks: list[Task]) -> None:
    payload = {
        "description": "Real bug-fix commits from open-source projects (see common.py).",
        "tasks": [asdict(t) for t in sorted(tasks, key=lambda t: t.id)],
    }
    MANIFEST.write_text(json.dumps(payload, indent=2) + "\n")
