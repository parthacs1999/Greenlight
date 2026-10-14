"""Mine real bug-fix commits from open-source repos and keep only ones that validate.

For each repo, scan recent commits that change both source and tests, whose message
looks like a bug fix, and whose source change is small. A candidate becomes a task
only if, in a workspace built exactly like the eval builds it:

1. at the fix commit, (nearly) the whole test suite passes, so regressions can be detected
   (the few tests that fail there for environment reasons are recorded and ignored);
2. with the fix's source change reverted, some tests in the fix's test files fail
   (FAIL_TO_PASS), while the test files still import (it's a bug, not a missing API);
3. those same tests pass at the fix commit;
4. the package is imported from the workspace, not from site-packages.

Usage:
    python evals/realworld/mine.py                     # all repos below
    python evals/realworld/mine.py --repos toolz --max-per-repo 3
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import (  # noqa: E402
    Repo,
    Task,
    clone,
    ensure_env,
    git,
    load_manifest,
    materialize,
    run_pytest,
    save_manifest,
)

REPOS = [
    Repo("more-itertools", "https://github.com/more-itertools/more-itertools", "more-itertools"),
    Repo("toolz", "https://github.com/pytoolz/toolz", "toolz"),
    Repo("python-slugify", "https://github.com/un33k/python-slugify", "python-slugify"),
    Repo("cachetools", "https://github.com/tkem/cachetools", "cachetools", src_root="src"),
    Repo("tabulate", "https://github.com/astanin/python-tabulate", "tabulate"),
    Repo("sqlparse", "https://github.com/andialbrecht/sqlparse", "sqlparse"),
    Repo("semver", "https://github.com/python-semver/python-semver", "semver", src_root="src"),
    Repo("boltons", "https://github.com/mahmoud/boltons", "boltons"),
    Repo("parse", "https://github.com/r1chardj0n3s/parse", "parse"),
    Repo(
        "humanize",
        "https://github.com/python-humanize/humanize",
        "humanize",
        src_root="src",
        deps=("pytest", "freezegun"),
    ),
]

FIX_MESSAGE = re.compile(
    r"\b(fix(e[sd])?|bug|regression|incorrect|wrong|broken|crash(es)?|handle|correct)\b", re.I
)
SKIP_MESSAGE = re.compile(r"\b(typo|docs?|readme|ci|lint|format|bump|release|version)\b", re.I)
MAX_SRC_LINES = 40
MAX_SRC_FILES = 2


def is_test_path(path: str) -> bool:
    name = Path(path).name
    return (
        "test" in Path(path).parts[0].lower()
        or name.startswith("test_")
        or name.endswith("_test.py")
        or "/tests/" in f"/{path}"
    )


def import_name(repo: Repo) -> str:
    return repo.package.replace("python-", "").replace("-", "_")


def candidates(clone_dir: Path, limit: int):
    shas = git(clone_dir, "log", "--no-merges", f"-n{limit}", "--format=%H").split()
    for sha in shas:
        message = git(clone_dir, "log", "-1", "--format=%s", sha).strip()
        if not FIX_MESSAGE.search(message) or SKIP_MESSAGE.search(message):
            continue
        stats = git(clone_dir, "show", "--numstat", "--format=", sha).strip().splitlines()
        src, tests, src_lines = [], [], 0
        for line in stats:
            added, deleted, path = line.split("\t", 2)
            if not path.endswith(".py") or "=>" in path:
                continue
            if is_test_path(path):
                tests.append(path)
            else:
                src.append(path)
                src_lines += int(added) + int(deleted) if added != "-" else 999
        if src and tests and len(src) <= MAX_SRC_FILES and src_lines <= MAX_SRC_LINES:
            date = git(clone_dir, "log", "-1", "--format=%cs", sha).strip()
            yield sha, message, date, src, tests, src_lines


def validate(repo: Repo, python: Path, task: Task, suite_cache: dict) -> tuple[bool, str]:
    # 1) full suite passes at the fix (cached per commit)
    if task.fix_sha not in suite_cache:
        temp_dir, ws = materialize(task, buggy=False)
        try:
            code, outcomes = run_pytest(python, ws, task.src_root, list(repo.test_paths))
            probe = subprocess.run(
                [str(python), "-c", f"import {import_name(repo)} as m; print(m.__file__)"],
                cwd=ws, env={"PYTHONPATH": task.src_root, "PATH": ""}, capture_output=True,
                text=True,
            )  # fmt: skip
            imported_from_ws = str(ws.resolve()) in str(Path(probe.stdout.strip()).resolve())
            failing = [t for t, o in outcomes.items() if o == "failed"]
            fix_outcomes_ok = bool(outcomes) and len(failing) <= max(3, len(outcomes) // 50)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        suite_cache[task.fix_sha] = (fix_outcomes_ok, imported_from_ws, outcomes)
    suite_ok, imported_from_ws, fixed_outcomes = suite_cache[task.fix_sha]
    if not suite_ok:
        return False, "too many tests fail at the fix commit in this environment"
    if not imported_from_ws:
        return False, "package not importable from the workspace"

    # 2) + 3) the fix's tests: which fail with the bug restored but pass with the fix
    temp_dir, ws = materialize(task, buggy=True)
    try:
        _, buggy = run_pytest(python, ws, task.src_root, task.test_files)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    if not buggy:
        return False, "test files don't import with the bug restored"
    f2p = sorted(
        t
        for t, outcome in buggy.items()
        if outcome == "failed" and fixed_outcomes.get(t) == "passed"
    )
    if not f2p:
        return False, "no FAIL_TO_PASS tests"
    if not any(outcome == "passed" for outcome in buggy.values()) and len(buggy) > 1:
        return False, "every test fails with the bug restored (likely a missing API, not a bug)"
    task.fail_to_pass = f2p
    task.fails_at_fix = sorted(t for t, o in fixed_outcomes.items() if o == "failed")
    p2p = sum(o == "passed" for o in fixed_outcomes.values())
    return True, f"{len(f2p)} FAIL_TO_PASS, {p2p} PASS_TO_PASS"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repos", nargs="*", help="Repo names (default: all)")
    parser.add_argument("--max-per-repo", type=int, default=3)
    parser.add_argument("--scan", type=int, default=400, help="Commits to scan per repo")
    args = parser.parse_args()

    existing = {t.id: t for t in load_manifest()}
    repos = [r for r in REPOS if not args.repos or r.name in args.repos]
    for repo in repos:
        print(f"\n== {repo.name}")
        try:
            clone_dir = clone(repo.url, repo.name)
            python = ensure_env(repo, clone_dir)
        except subprocess.CalledProcessError as exc:
            print(f"   setup failed: {exc}")
            continue
        found = sum(t.repo == repo.name for t in existing.values())
        suite_cache: dict = {}
        for sha, message, date, src, tests, lines in candidates(clone_dir, args.scan):
            if found >= args.max_per_repo:
                break
            task_id = f"{repo.name}-{sha[:7]}"
            if task_id in existing:
                continue
            task = Task(
                id=task_id, repo=repo.name, url=repo.url, fix_sha=sha, message=message,
                date=date, src_files=src, test_files=tests, fail_to_pass=[],
                src_lines_changed=lines, src_root=repo.src_root,
                test_paths=list(repo.test_paths),
            )  # fmt: skip
            ok, reason = validate(repo, python, task, suite_cache)
            print(f"   {'✓' if ok else '·'} {sha[:7]} {message[:60]!r}: {reason}")
            if ok:
                existing[task_id] = task
                found += 1
                save_manifest(list(existing.values()))
    print(f"\nManifest: {len(existing)} tasks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
