"""The agent's tools, bound to one workspace (the repository being fixed).

Design choices:
- There is no general shell tool. The model can only run the test command the
  *user* configured (optionally narrowed to one test file or test id), which removes
  a whole class of prompt-injection and data-exfiltration risks.
- Edits are exact string replacements, so the model never rewrites whole files.
- Test files are read-only by default, so the agent can't "fix" a failure by
  weakening the test.
- Every tool returns a string; failures come back as (message, is_error=True) so the
  model can recover instead of the run crashing.
"""

from __future__ import annotations

import difflib
import fnmatch
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .security import IGNORED_DIRS, PathDenied, is_secret_file, resolve_in_workspace, scrubbed_env

MAX_OUTPUT_CHARS = 12_000
MAX_LIST_ENTRIES = 400
MAX_SEARCH_HITS = 100
COMMAND_NOT_FOUND = 127

TEST_FILE_PATTERNS = ("test_*.py", "*_test.py", "conftest.py", "*.test.*", "*.spec.*")
TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__"})


class ToolError(Exception):
    """A recoverable tool failure; the message is shown to the model."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the arguments
    handler: Callable[..., str]


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS, keep: str = "tail") -> str:
    if len(text) <= limit:
        return text
    note = f"\n... [{len(text) - limit} characters truncated] ...\n"
    if keep == "tail":  # test failures summarise at the end
        return note + text[-limit:]
    return text[:limit] + note


def is_test_file(relative: str) -> bool:
    path = Path(relative)
    if any(part in TEST_DIR_NAMES for part in path.parts[:-1]):
        return True
    return any(fnmatch.fnmatch(path.name, p) for p in TEST_FILE_PATTERNS)


ApproveFn = Callable[[str, str], bool]  # (relative path, unified diff) -> approved?


class Workspace:
    def __init__(
        self,
        root: Path,
        test_command: str,
        *,
        allow_test_edits: bool = False,
        deny_patterns: tuple[str, ...] = (),
        test_timeout: int = 300,
        approve: ApproveFn | None = None,
    ) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise ValueError(f"{root} is not a directory")
        self.test_command = test_command
        self.test_argv = shlex.split(test_command)
        if not self.test_argv:
            raise ValueError("test command is empty")
        self.allow_test_edits = allow_test_edits
        self.deny_patterns = deny_patterns
        self.test_timeout = test_timeout
        self.approve = approve
        # Original contents of every file the agent changed (None = file was created).
        self.originals: dict[str, str | None] = {}

    # ---- helpers -------------------------------------------------------------

    def _resolve(self, relative: str) -> Path:
        try:
            return resolve_in_workspace(self.root, relative, self.deny_patterns)
        except PathDenied as exc:
            raise ToolError(str(exc)) from exc

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix() or "."

    def _walk(self, start: Path):
        for path in sorted(start.rglob("*")):
            rel_parts = path.relative_to(self.root).parts
            if any(part in IGNORED_DIRS for part in rel_parts):
                continue
            if any(is_secret_file(part, self.deny_patterns) for part in rel_parts):
                continue
            yield path

    def _check_writable(self, relative: str, path: Path) -> None:
        rel = self._rel(path)
        if not self.allow_test_edits and is_test_file(rel):
            raise ToolError(
                f"'{relative}' is a test file and test files are read-only. "
                "Fix the code under test instead."
            )

    def _write(self, path: Path, new_content: str) -> None:
        rel = self._rel(path)
        old_content = path.read_text(encoding="utf-8") if path.exists() else None
        if self.approve is not None:
            diff = _unified_diff(rel, old_content or "", new_content)
            if not self.approve(rel, diff):
                raise ToolError("The user rejected this change. Try a different approach.")
        self.originals.setdefault(rel, old_content)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_content, encoding="utf-8")

    # ---- tools ---------------------------------------------------------------

    def list_files(self, path: str = ".") -> str:
        start = self._resolve(path)
        if not start.is_dir():
            raise ToolError(f"'{path}' is not a directory")
        entries = [self._rel(p) + ("/" if p.is_dir() else "") for p in self._walk(start)]
        if not entries:
            return "(empty directory)"
        if len(entries) > MAX_LIST_ENTRIES:
            hidden = len(entries) - MAX_LIST_ENTRIES
            entries = entries[:MAX_LIST_ENTRIES] + [f"... and {hidden} more"]
        return "\n".join(entries)

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"'{path}' does not exist or is not a file")
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ToolError(f"'{path}' is not a text file") from exc
        start = max(start_line, 1)
        end = min(end_line or len(lines), len(lines))
        numbered = "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1))
        return truncate(numbered or "(empty file)", keep="head")

    def search(self, pattern: str, path: str = ".") -> str:
        start = self._resolve(path)
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid regex: {exc}") from exc
        files = [start] if start.is_file() else [p for p in self._walk(start) if p.is_file()]
        hits: list[str] = []
        for file in files:
            try:
                text = file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{self._rel(file)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        return "\n".join(hits) + f"\n... (stopped at {MAX_SEARCH_HITS} matches)"
        return "\n".join(hits) or "No matches."

    def edit_file(self, path: str, old_str: str, new_str: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"'{path}' does not exist; use write_file to create new files")
        self._check_writable(path, target)
        content = target.read_text(encoding="utf-8")
        count = content.count(old_str)
        if count == 0:
            raise ToolError("old_str was not found. Re-read the file and copy the text exactly.")
        if count > 1:
            raise ToolError(f"old_str matches {count} places; include more surrounding context.")
        self._write(target, content.replace(old_str, new_str, 1))
        return f"Edited {self._rel(target)}."

    def write_file(self, path: str, content: str) -> str:
        target = self._resolve(path)
        if target.exists():
            raise ToolError(f"'{path}' already exists; use edit_file to change it")
        self._check_writable(path, target)
        self._write(target, content)
        return f"Created {self._rel(target)}."

    def run_tests(self, target: str | None = None) -> str:
        code, output = self.run_test_command(target)
        status = "PASSED" if code == 0 else f"FAILED (exit code {code})"
        return f"{status}\n\n{output}"

    # ---- test execution (also used by the agent for independent verification) --

    def run_test_command(self, target: str | None = None) -> tuple[int, str]:
        argv = list(self.test_argv)
        if target:
            argv.append(self._validate_test_target(target))
        # No .pyc files: an edit made within the same second as the previous run (with
        # the same file size) would otherwise be shadowed by stale cached bytecode.
        env = {**scrubbed_env(), "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            proc = subprocess.run(
                argv,
                cwd=self.root,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.test_timeout,
            )
        except FileNotFoundError:
            return COMMAND_NOT_FOUND, f"Test command not found: {self.test_argv[0]}"
        except subprocess.TimeoutExpired:
            return 124, f"Tests timed out after {self.test_timeout}s"
        output = (proc.stdout + ("\n" + proc.stderr if proc.stderr else "")).strip()
        return proc.returncode, truncate(output or "(no output)")

    def _validate_test_target(self, target: str) -> str:
        """Allow `path` or `path::test_id`, inside the workspace; never extra flags."""
        if target.startswith("-") or any(c.isspace() for c in target):
            raise ToolError("target must be a single test path such as tests/test_x.py::test_y")
        file_part = target.split("::", 1)[0]
        self._resolve(file_part)
        return target

    # ---- reporting -------------------------------------------------------------

    def diff(self) -> str:
        chunks = []
        for rel, original in sorted(self.originals.items()):
            current_path = self.root / rel
            current = current_path.read_text(encoding="utf-8") if current_path.exists() else ""
            chunks.append(_unified_diff(rel, original or "", current))
        return "".join(chunks)

    # ---- tool registry ---------------------------------------------------------

    def tools(self) -> list[Tool]:
        path_prop = {"type": "string", "description": "Path relative to the repository root."}
        return [
            Tool(
                "list_files",
                "List files and directories (recursively) under a path in the repository.",
                _schema({"path": {**path_prop, "default": "."}}),
                self.list_files,
            ),
            Tool(
                "read_file",
                "Read a text file. Lines are prefixed with line numbers. "
                "Optionally pass start_line/end_line to read part of a large file.",
                _schema(
                    {
                        "path": path_prop,
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    required=["path"],
                ),
                self.read_file,
            ),
            Tool(
                "search",
                "Search file contents with a Python regular expression. "
                "Returns matching lines as path:line: text.",
                _schema(
                    {"pattern": {"type": "string"}, "path": {**path_prop, "default": "."}},
                    required=["pattern"],
                ),
                self.search,
            ),
            Tool(
                "edit_file",
                "Replace one exact occurrence of old_str with new_str in an existing file. "
                "old_str must match the file exactly (whitespace included) and be unique; "
                "include surrounding lines if needed. Test files are read-only.",
                _schema(
                    {
                        "path": path_prop,
                        "old_str": {"type": "string"},
                        "new_str": {"type": "string"},
                    },
                    required=["path", "old_str", "new_str"],
                ),
                self.edit_file,
            ),
            Tool(
                "write_file",
                "Create a NEW file with the given content. Fails if the file exists.",
                _schema(
                    {"path": path_prop, "content": {"type": "string"}},
                    required=["path", "content"],
                ),
                self.write_file,
            ),
            Tool(
                "run_tests",
                f"Run the project's test command (`{self.test_command}`). Optionally pass a "
                "target (a test file or file::test_name) to run a subset.",
                _schema({"target": {"type": "string"}}),
                self.run_tests,
            ),
        ]

    def execute(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Run a tool by name. Returns (output, is_error)."""
        tool = next((t for t in self.tools() if t.name == name), None)
        if tool is None:
            return f"Unknown tool '{name}'", True
        allowed = set(tool.parameters["properties"])
        unexpected = set(args) - allowed
        if unexpected:
            return f"Unexpected arguments for {name}: {sorted(unexpected)}", True
        missing = [p for p in tool.parameters.get("required", []) if p not in args]
        if missing:
            return f"Missing required arguments for {name}: {missing}", True
        try:
            return tool.handler(**args), False
        except ToolError as exc:
            return f"Error: {exc}", True
        except (TypeError, ValueError, OSError) as exc:
            return f"Error: {type(exc).__name__}: {exc}", True


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _unified_diff(rel: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )
