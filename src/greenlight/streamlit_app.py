"""Streamlit UI for Greenlight.

Every visitor brings their own API key. The UI never reads keys from the server's
environment or keychain, not even locally, so whoever runs the app never pays for
someone else's run and never exposes their own key.

Modes:
- Hosted (the default, e.g. Streamlit Community Cloud): bundled demo projects only,
  always on a temp copy, with a fixed test command.
- Local (`greenlight ui` sets GREENLIGHT_LOCAL=1): you can also point it at a folder
  on your machine and choose the test command.

Hosted is the default so a misconfigured deployment fails safe.

Rendering rule: anything derived from a repository or the model (file names, file
contents, test output, the model's text) goes through `esc()` before it is placed in
HTML, and HTML is rendered with `st.html`, which strips scripts and applies no
Markdown. Larger blobs are shown with `st.code` / `st.text`.
"""

from __future__ import annotations

import difflib
import html
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st

from greenlight.agent import Agent, Budget, RunResult
from greenlight.pricing import price_for
from greenlight.providers import create_provider
from greenlight.security import forget_secret, redact, register_env_secrets, register_secret
from greenlight.tools import Workspace

REPO_URL = "https://github.com/parthacs1999/coding-agent"

# Hosted-mode limits, so one visitor can't exhaust a small free server.
HOSTED_MAX_STEPS = 20
HOSTED_TEST_TIMEOUT = 60
# Shared by all sessions in this server process (the module is imported once).
_RUN_SLOTS = threading.BoundedSemaphore(int(os.environ.get("GREENLIGHT_MAX_CONCURRENT_RUNS", "2")))


@dataclass(frozen=True)
class ProviderInfo:
    label: str
    company: str
    default_model: str
    key_url: str
    key_hint: str
    key_prefixes: tuple[str, ...]


PROVIDERS = {
    "gemini": ProviderInfo(
        "Gemini",
        "Google",
        "gemini-3.8-flash",
        "https://aistudio.google.com/apikey",
        "AIza…",
        ("AIza", "AQ."),
    ),
    "claude": ProviderInfo(
        "Claude",
        "Anthropic",
        "claude-opus-5-5",
        "https://console.anthropic.com/settings/keys",
        "sk-ant-…",
        ("sk-ant-",),
    ),
}


@dataclass(frozen=True)
class DemoTask:
    title: str
    emoji: str
    symptom: str
    level: str


# Symptoms only: the page shouldn't hand the agent (or the visitor) the answer.
DEMO_TASKS = {
    "cross_module_discount": DemoTask(
        "Shopping cart", "🛒", "Cart totals go negative when a discount is applied.", "Multi-file"
    ),
    "pagination": DemoTask("Pagination", "📄", "Pages show the wrong items and count.", "Easy"),
    "mutable_default": DemoTask(
        "Article tags", "🏷️", "Articles mysteriously share tags with each other.", "Classic trap"
    ),
    "csv_quoted_fields": DemoTask(
        "CSV parser", "📑", "Quoted fields containing commas are split apart.", "Medium"
    ),
    "retry_backoff": DemoTask(
        "Retry backoff", "⏱️", "Retry delays are too long and ignore the cap.", "Medium"
    ),
    "lru_cache": DemoTask("LRU cache", "🗃️", "The cache evicts the wrong entries.", "Hard"),
    "interval_merge": DemoTask(
        "Calendar merge", "📅", "Overlapping time ranges aren't merged correctly.", "Hard"
    ),
    "inventory_service": DemoTask(
        "Inventory service",
        "📦",
        "Reserved stock is lost, and failed orders half-apply.",
        "Hard · 3 files",
    ),
    "split_bill": DemoTask("Bill splitter", "🧾", "Split bills don't add up to the total.", "Hard"),
    "config_parser": DemoTask(
        "Config parser", "⚙️", "Several settings are read with the wrong values.", "Hard · 5 bugs"
    ),
}

STAGES = ("Reproduce", "Investigate", "Fix", "Verify")

VERDICTS = {
    "fixed": ("good", "✅", "Fixed and verified"),
    "already_passing": ("good", "✅", "Nothing to fix: the tests already pass"),
    "not_fixed": ("bad", "❌", "Not fixed: the tests still fail"),
    "budget_exceeded": ("warn", "⏸️", "Stopped at the budget before the tests passed"),
    "error": ("bad", "⚠️", "The run couldn't complete"),
}

CSS = """
<style>
  .block-container { max-width: 1120px; padding-top: 2rem; padding-bottom: 4rem; }
  [data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none; }
  .gl-hero { padding: 8px 0 4px; }
  .gl-badge { display: inline-block; font-size: 12.5px; font-weight: 600; color: #15803d;
              background: #dcfce7; border-radius: 999px; padding: 4px 12px; }
  .gl-hero h1 { font-size: 2.6rem; line-height: 1.12; letter-spacing: -0.03em;
                margin: 14px 0 10px; color: #0f172a; font-weight: 750; }
  .gl-hero h1 span { color: #16a34a; }
  .gl-hero p.lead { font-size: 1.08rem; color: #475569; max-width: 720px; margin: 0; }
  .gl-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
  .gl-chip { font-size: 13px; color: #334155; background: #f1f5f9; border: 1px solid #e2e8f0;
             border-radius: 999px; padding: 5px 12px; }
  .gl-how { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 26px 0 8px; }
  .gl-how div { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px;
                padding: 14px; font-size: 13.5px; color: #475569; }
  .gl-how b { display: block; color: #0f172a; font-size: 14px; margin-bottom: 3px; }
  .gl-how .n { display: inline-grid; place-items: center; width: 22px; height: 22px;
               border-radius: 50%; background: #16a34a; color: #fff; font-size: 12px;
               font-weight: 700; margin-bottom: 8px; }
  @media (max-width: 760px) {
    .gl-how { grid-template-columns: 1fr 1fr; }
    .gl-hero h1 { font-size: 1.9rem; }
  }
  .gl-step { font-size: 12px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
             color: #16a34a; margin-bottom: 2px; }
  .gl-h { font-size: 1.15rem; font-weight: 700; color: #0f172a; margin: 0 0 4px; }
  .gl-muted { color: #64748b; font-size: 13.5px; }
  .gl-task { border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 16px;
             background: #f8fafc; margin-top: 4px; }
  .gl-task .t { font-weight: 700; color: #0f172a; font-size: 15px; }
  .gl-task .lvl { font-size: 11.5px; font-weight: 600; color: #475569; background: #e2e8f0;
                  border-radius: 999px; padding: 2px 8px; margin-left: 6px; vertical-align: 2px; }
  .gl-task .s { color: #475569; margin-top: 2px; }
  .gl-status { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px;
               font-weight: 600; border-radius: 999px; padding: 3px 10px; margin-top: 10px; }
  .gl-status.bad { color: #b91c1c; background: #fee2e2; }
  .gl-status.good { color: #15803d; background: #dcfce7; }
  .gl-trust { border: 1px solid #bbf7d0; background: #f0fdf4; border-radius: 12px;
              padding: 12px 14px; font-size: 13.5px; color: #14532d; }
  .gl-trust b { color: #14532d; }
  .gl-stages { display: flex; align-items: center; gap: 0; margin: 6px 0 14px; }
  .gl-stage { display: flex; align-items: center; gap: 8px; font-size: 13.5px; font-weight: 600;
              color: #94a3b8; white-space: nowrap; }
  .gl-stage .dot { width: 26px; height: 26px; border-radius: 50%; display: grid;
                   place-items: center; font-size: 12px; border: 2px solid #cbd5e1;
                   background: #fff; color: #94a3b8; }
  .gl-stage.done { color: #15803d; }
  .gl-stage.done .dot { background: #16a34a; border-color: #16a34a; color: #fff; }
  .gl-stage.active { color: #0f172a; }
  .gl-stage.active .dot { border-color: #16a34a; color: #16a34a;
                          box-shadow: 0 0 0 4px #dcfce7; }
  .gl-stage.failed .dot { background: #ef4444; border-color: #ef4444; color: #fff; }
  .gl-line { flex: 1; height: 2px; background: #e2e8f0; margin: 0 10px; min-width: 16px; }
  .gl-line.done { background: #16a34a; }
  .gl-stats { display: flex; flex-wrap: wrap; gap: 18px; font-size: 13px; color: #64748b; }
  .gl-stats b { color: #0f172a; }
  .gl-item { display: grid; grid-template-columns: 30px 1fr auto; gap: 10px; align-items: start;
             padding: 9px 2px; border-bottom: 1px solid #f1f5f9; }
  .gl-ico { width: 30px; height: 30px; border-radius: 8px; background: #f1f5f9;
            display: grid; place-items: center; font-size: 15px; }
  .gl-item .what { color: #0f172a; font-size: 14px; overflow-wrap: anywhere; }
  .gl-item .what code { background: #f1f5f9; color: #0f172a; padding: 1px 6px; border-radius: 6px;
                        font-size: 12.5px; }
  .gl-item .sub { color: #64748b; font-size: 12.5px; margin-top: 1px; overflow-wrap: anywhere; }
  .gl-pill { font-size: 12px; font-weight: 600; border-radius: 999px; padding: 2px 9px;
             white-space: nowrap; }
  .gl-pill.run { color: #1d4ed8; background: #dbeafe; }
  .gl-pill.ok { color: #15803d; background: #dcfce7; }
  .gl-pill.bad { color: #b91c1c; background: #fee2e2; }
  .gl-pill.guard { color: #92400e; background: #fef3c7; }
  .gl-thought { color: #475569; font-style: italic; font-size: 13.5px; padding: 8px 12px;
                border-left: 3px solid #e2e8f0; margin: 6px 0; white-space: pre-wrap; }
  .gl-verdict { border-radius: 14px; padding: 18px 20px; margin: 4px 0 12px; }
  .gl-verdict.good { background: #f0fdf4; border: 1px solid #bbf7d0; }
  .gl-verdict.bad { background: #fef2f2; border: 1px solid #fecaca; }
  .gl-verdict.warn { background: #fffbeb; border: 1px solid #fde68a; }
  .gl-verdict .v { font-size: 1.3rem; font-weight: 750; color: #0f172a; }
  .gl-verdict .ba { margin-top: 6px; font-size: 14px; color: #334155; }
  .gl-verdict .ba .x { color: #b91c1c; font-weight: 600; }
  .gl-verdict .ba .y { color: #15803d; font-weight: 600; }
  .gl-verdict .why { margin-top: 4px; font-size: 13.5px; color: #475569; }
  .gl-footer { margin-top: 40px; padding-top: 16px; border-top: 1px solid #e2e8f0;
               font-size: 13px; color: #64748b; }
  .gl-footer a { color: #15803d; }
</style>
"""


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def is_local() -> bool:
    return os.environ.get("GREENLIGHT_LOCAL") == "1"


def tasks_dir() -> Path:
    candidates = [
        os.environ.get("GREENLIGHT_TASKS_DIR"),
        Path.cwd() / "evals" / "tasks",
        Path(__file__).resolve().parents[2] / "evals" / "tasks",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return Path("evals/tasks")


def available_tasks() -> list[str]:
    root = tasks_dir()
    if not root.is_dir():
        return []
    found = {p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")}
    ordered = [name for name in DEMO_TASKS if name in found]
    return ordered + sorted(found - set(ordered))


def task_info(name: str) -> DemoTask:
    return DEMO_TASKS.get(name, DemoTask(name, "🐞", "A bug that makes the tests fail.", ""))


def python_test_command() -> str:
    return f"{sys.executable} -m pytest -q -p no:cacheprovider"


def copy_task(name: str) -> tuple[Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="greenlight-ui-"))
    repo = temp_dir / name
    try:
        shutil.copytree(
            tasks_dir() / name,
            repo,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".greenlight"),
        )
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return temp_dir, repo


# ---- test output helpers -------------------------------------------------------------

_COUNT = re.compile(r"(\d+) (failed|passed|errors?|skipped)")


def test_counts(output: str) -> str:
    """'1 failed, 3 passed' from pytest's summary line, or '' if not recognised."""
    lines = [ln for ln in output.strip().splitlines() if _COUNT.search(ln)]
    if not lines:
        return ""
    return ", ".join(f"{n} {word}" for n, word in _COUNT.findall(lines[-1]))


@st.cache_data(show_spinner=False)
def preview_failure(task: str) -> tuple[str, str]:
    """Run a demo project's tests once so the visitor can see it's really broken."""
    temp_dir, repo = copy_task(task)
    try:
        code, output = Workspace(repo, python_test_command()).run_test_command()
        return test_counts(output) or f"exit code {code}", output
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def source_files(task: str) -> list[tuple[str, str]]:
    root = tasks_dir() / task
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(root.glob("*.py"))]


# ---- live run view -------------------------------------------------------------------


class RunView:
    """Renders agent events as a stage tracker plus a human-readable activity feed."""

    def __init__(self, container) -> None:
        self.stage = 0
        self.failed = False
        self.steps = 0
        self.tokens = 0
        self.started = time.monotonic()
        self.pending: list[tuple[Any, Any, dict[str, Any]]] = []
        with container:
            self.stages_ph = st.empty()
            self.stats_ph = st.empty()
            self.feed = st.container()
        self._render_header()

    # header ---------------------------------------------------------------------------

    def _render_header(self, elapsed: float | None = None) -> None:
        parts = []
        for i, name in enumerate(STAGES):
            if i < self.stage:
                cls, mark = "done", "✓"
            elif i == self.stage:
                cls, mark = ("failed", "!") if self.failed else ("active", str(i + 1))
            else:
                cls, mark = "", str(i + 1)
            parts.append(f'<div class="gl-stage {cls}"><div class="dot">{mark}</div>{name}</div>')
            if i < len(STAGES) - 1:
                line_cls = "done" if i < self.stage else ""
                parts.append(f'<div class="gl-line {line_cls}"></div>')
        self.stages_ph.html(f'<div class="gl-stages">{"".join(parts)}</div>')
        seconds = elapsed if elapsed is not None else time.monotonic() - self.started
        self.stats_ph.html(
            '<div class="gl-stats">'
            f"<span>Step <b>{self.steps}</b></span>"
            f"<span><b>{self.tokens:,}</b> tokens</span>"
            f"<span><b>{seconds:.0f}s</b> elapsed</span></div>"
        )

    def _advance(self, stage: int) -> None:
        self.stage = max(self.stage, stage)

    # feed -----------------------------------------------------------------------------

    def _item(
        self, target, icon: str, what: str, sub: str = "", pill: tuple[str, str] | None = None
    ):
        pill_html = (
            f'<span class="gl-pill {pill[0]}">{esc(pill[1])}</span>' if pill else "<span></span>"
        )
        sub_html = f'<div class="sub">{sub}</div>' if sub else ""
        target.html(
            f'<div class="gl-item"><div class="gl-ico">{icon}</div>'
            f'<div><div class="what">{what}</div>{sub_html}</div>{pill_html}</div>'
        )

    @staticmethod
    def _describe(name: str, args: dict[str, Any]) -> tuple[str, str, str]:
        """(icon, sentence, detail) for a tool call. All values escaped."""
        path = f"<code>{esc(args.get('path', '.'))}</code>"
        if name == "read_file":
            lines = ""
            if "start_line" in args or "end_line" in args:
                lines = f"lines {esc(args.get('start_line', 1))}–{esc(args.get('end_line', 'end'))}"
            return "📄", f"Read {path}", lines
        if name == "list_files":
            return "📂", f"Listed files in {path}", ""
        if name == "search":
            return "🔎", f"Searched for <code>{esc(args.get('pattern', ''))}</code>", ""
        if name == "edit_file":
            return "✏️", f"Edited {path}", ""
        if name == "write_file":
            return "➕", f"Created {path}", ""
        if name == "run_tests":
            target = args.get("target")
            return "🧪", "Ran the tests" + (f" in <code>{esc(target)}</code>" if target else ""), ""
        return "•", f"<code>{esc(name)}</code>", ""

    def on_event(self, kind: str, data: dict[str, Any]) -> None:
        data = {k: redact(v) if isinstance(v, str) else v for k, v in data.items()}
        if kind == "initial_tests":
            counts = test_counts(data["output"])
            if data["exit_code"] == 0:
                self._item(
                    self.feed, "🧪", "Ran the tests: they already pass", counts, ("ok", "passing")
                )
            else:
                self._item(
                    self.feed,
                    "🧪",
                    "Reproduced the failure",
                    esc(counts or f"exit code {data['exit_code']}"),
                    ("bad", "failing"),
                )
                with self.feed.expander("Failing test output"):
                    st.code(tail(data["output"], 30), language=None)
                self._advance(1)
        elif kind == "model_turn":
            self.steps = data["step"]
            usage = data.get("usage") or {}
            self.tokens += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            if data["text"].strip() and data["tool_calls"]:
                self.feed.html(f'<div class="gl-thought">💭 {esc(data["text"].strip())}</div>')
            for call in data["tool_calls"]:
                box = self.feed.container()
                line = box.empty()
                icon, what, sub = self._describe(call["name"], call["args"])
                self._item(line, icon, what, sub, ("run", "working…"))
                self.pending.append((box, line, call))
                if call["name"] in ("edit_file", "write_file"):
                    self._advance(2)
                elif call["name"] == "run_tests" and self.stage >= 2:
                    self._advance(3)
        elif kind == "tool_result":
            if not self.pending:
                return
            box, line, call = self.pending.pop(0)
            icon, what, sub = self._describe(call["name"], call["args"])
            output = data["output"]
            if data["is_error"]:
                guarded = any(w in output for w in ("secret", "read-only", "outside", "protected"))
                pill = ("guard", "🛡️ blocked") if guarded else ("bad", "error")
                self._item(line, icon, what, esc(output.removeprefix("Error: ")[:240]), pill)
            elif call["name"] == "run_tests":
                passed = output.startswith("PASSED")
                counts = test_counts(output)
                self._item(
                    line,
                    icon,
                    what,
                    esc(counts),
                    ("ok", "passing") if passed else ("bad", "failing"),
                )
                if not passed:
                    with box.expander("Test output"):
                        st.code(tail(output, 25), language=None)
            elif call["name"] == "search":
                hits = 0 if output.strip() == "No matches." else len(output.strip().splitlines())
                self._item(
                    line, icon, what, f"{hits} match{'es' if hits != 1 else ''}", ("ok", "done")
                )
            elif call["name"] == "read_file":
                n = len(output.splitlines())
                self._item(line, icon, what, sub or f"{n} lines", ("ok", "done"))
            elif call["name"] == "edit_file":
                self._item(line, icon, what, "", ("ok", "applied"))
                box.code(redact(mini_diff(call["args"])), language="diff")
            else:
                self._item(line, icon, what, sub, ("ok", "done"))
        elif kind == "budget_stop":
            self._item(self.feed, "⏸️", "Stopped", esc(data["reason"]), ("bad", "budget"))
        elif kind == "error":
            self.failed = True
            self._item(
                self.feed,
                "⚠️",
                "The AI provider returned an error",
                esc(data["message"]),
                ("bad", "error"),
            )
        elif kind == "final_tests":
            self._advance(3)
            counts = test_counts(data["output"])
            if data["exit_code"] == 0:
                self._item(
                    self.feed,
                    "✅",
                    "Verified independently: the test suite passes",
                    esc(counts),
                    ("ok", "verified"),
                )
                self.stage = len(STAGES)
            else:
                self.failed = True
                self._item(
                    self.feed,
                    "❌",
                    "Verified independently: the tests still fail",
                    esc(counts),
                    ("bad", "failing"),
                )
        self._render_header()

    def finish(self, duration: float) -> None:
        self._render_header(elapsed=duration)


def tail(text: str, lines: int = 25) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def mini_diff(args: dict[str, Any]) -> str:
    old = str(args.get("old_str", "")).splitlines()
    new = str(args.get("new_str", "")).splitlines()
    body = [ln for ln in difflib.unified_diff(old, new, lineterm="", n=2)][2:]
    return "\n".join(body) or "(whitespace-only change)"


# ---- page sections -------------------------------------------------------------------


def hero() -> None:
    st.html(
        '<div class="gl-hero">'
        '<span class="gl-badge">Open-source AI agent · Bring your own key</span>'
        "<h1>Failing tests? <span>Greenlight</span> finds the bug and fixes it.</h1>"
        '<p class="lead">Pick a broken project and connect your own Claude or Gemini key. '
        "An AI agent reproduces the failure, reads the code to find the root cause, makes a "
        "minimal fix, and then proves it works by re-running the tests itself.</p>"
        '<div class="gl-chips">'
        '<span class="gl-chip">✅ Every fix verified by your own tests</span>'
        '<span class="gl-chip">🧪 Never weakens or edits tests</span>'
        '<span class="gl-chip">🔒 Your key is never stored</span>'
        '<span class="gl-chip">💸 Hard step and cost limits</span>'
        "</div>"
        '<div class="gl-how">'
        '<div><span class="n">1</span><b>Reproduce</b>Runs the test suite and captures the failure.</div>'
        '<div><span class="n">2</span><b>Investigate</b>Searches and reads the code to find the root cause.</div>'
        '<div><span class="n">3</span><b>Fix</b>Makes small, exact edits to the source, never the tests.</div>'
        '<div><span class="n">4</span><b>Verify</b>Re-runs the tests itself instead of trusting the AI.</div>'
        "</div></div>"
    )


def step_header(number: int, title: str, subtitle: str) -> None:
    st.html(
        f'<div class="gl-step">Step {number}</div><div class="gl-h">{esc(title)}</div>'
        f'<div class="gl-muted">{esc(subtitle)}</div>'
    )


def choose_project() -> dict[str, Any] | None:
    step_header(1, "Choose a broken project", "Each one has a real bug and failing tests.")
    source = "demo"
    if is_local():
        source = st.segmented_control(
            "Project",
            ["demo", "path"],
            default="demo",
            required=True,
            format_func={"demo": "Demo project", "path": "My own folder"}.get,
            key="source",
            label_visibility="collapsed",
        )

    if source == "path":
        path = st.text_input("Folder path", placeholder="/Users/you/projects/my-app")
        test_cmd = st.text_input("Test command", value=python_test_command())
        st.caption(
            "⚠️ Edits are written to this folder. Commit or branch first so you can review and revert."
        )
        return {"source": "path", "path": path.strip(), "test_cmd": test_cmd.strip(), "label": path}

    tasks = available_tasks()
    if not tasks:
        st.error(f"No demo projects found in {tasks_dir()}.")
        return None
    task = (
        st.pills(
            "Demo project",
            tasks,
            default=tasks[0],
            required=True,
            format_func=lambda t: f"{task_info(t).emoji} {task_info(t).title}",
            key="task",
            label_visibility="collapsed",
        )
        or tasks[0]
    )

    info = task_info(task)
    counts, output = preview_failure(task)
    level = f'<span class="lvl">{esc(info.level)}</span>' if info.level else ""
    st.html(
        f'<div class="gl-task"><div class="t">{info.emoji} {esc(info.title)}{level}</div>'
        f'<div class="s">{esc(info.symptom)}</div>'
        f'<span class="gl-status bad">● Tests right now: {esc(counts)}</span></div>'
    )
    with st.expander("Peek at the code and the failing tests"):
        tabs = st.tabs(["Test output", *[name for name, _ in source_files(task)]])
        tabs[0].code(tail(output, 40), language=None)
        for tab, (_, content) in zip(tabs[1:], source_files(task), strict=True):
            tab.code(content, language="python")
    st.caption(
        "The agent works on a temporary copy, so this project stays broken for the next visitor."
    )
    return {"source": "demo", "task": task, "label": f"{info.emoji} {info.title}"}


def connect_model() -> dict[str, Any]:
    step_header(
        2,
        "Connect your AI model",
        "Use your own API key. You only pay your provider for what you use.",
    )
    provider = st.segmented_control(
        "Provider",
        list(PROVIDERS),
        default="gemini",
        required=True,
        format_func=lambda p: f"{PROVIDERS[p].label} ({PROVIDERS[p].company})",
        key="provider",
        label_visibility="collapsed",
    )
    info = PROVIDERS[provider]
    key = st.text_input(
        f"{info.label} API key",
        type="password",
        placeholder=info.key_hint,
        key=f"api_key_{provider}",
    )
    if key and not key.strip().startswith(info.key_prefixes):
        st.caption(
            f"⚠️ This doesn't look like a {info.label} key ({info.key_hint}). Check you pasted the right one."
        )
    st.caption(
        f"No key yet? [Create a {info.label} API key ↗]({info.key_url}). It takes about a minute."
    )

    hosted = not is_local()
    st.html(
        '<div class="gl-trust">🔒 <b>Your key is used for this one run and then discarded.</b> '
        "It's never saved, logged or shown, and it's only sent to "
        f"{esc(info.company)}.</div>"
    )
    with st.expander("How your key is protected"):
        st.markdown(
            f"""
- **Used once, never stored.** The key lives in memory only while your run is active. It's
  never written to disk, a database, the run log or analytics, and the key field is cleared
  as soon as you press the button.
- **Sent only to {info.company}.** It goes directly to {info.company}'s API over HTTPS.
  {"This demo server passes it straight through and keeps nothing." if hosted else "Running locally, it never leaves your machine except for that call."}
- **Masked everywhere.** If anything key-shaped ever appears in files, test output or the
  AI's replies, it's replaced with `[REDACTED]` before you or the AI see it.
- **The AI can't reach secrets.** The agent can't open `.env` or key files, and the tests it
  runs get no API keys in their environment.
- **Hard spending limits.** Every run stops at the step and cost budget below.
- **Good practice:** create a separate key with a spending cap for trying out tools like
  this, and delete it afterwards.
- **Check it yourself.** It's open source: [read the code]({REPO_URL}).
"""
        )

    with st.expander("Advanced: model and budget"):
        model = st.text_input("Model", value=info.default_model, key=f"model_{provider}")
        cols = st.columns(2)
        max_steps = cols[0].number_input("Max steps", 1, 60 if is_local() else HOSTED_MAX_STEPS, 20)
        max_cost = cols[1].number_input("Max cost (USD)", 0.05, 10.0, 0.50, step=0.05)
        if price_for(model.strip()) is None:
            st.caption("No price is known for this model, so only the step limit applies.")
    return {
        "provider": provider,
        "api_key": key.strip(),
        "model": model.strip() or info.default_model,
        "max_steps": int(max_steps),
        "max_cost": float(max_cost),
    }


def capture_key(provider: str) -> None:
    """Button callback: move the pasted key out of the widget before the page re-renders.

    Callbacks run before the script, so blanking the widget value here also clears it in
    the browser. The key then lives only in this run's local variables.
    """
    widget = f"api_key_{provider}"
    st.session_state["_run_key"] = st.session_state.get(widget, "").strip()
    st.session_state[widget] = ""


def run_agent(project: dict[str, Any], model: dict[str, Any], container) -> None:
    if not _RUN_SLOTS.acquire(blocking=False):
        container.warning("Greenlight is busy with other runs right now. Try again in a minute.")
        return
    api_key = model["api_key"]
    register_secret(api_key)
    hosted = not is_local()

    temp_dir: Path | None = None
    try:
        if project["source"] == "demo":
            temp_dir, repo = copy_task(project["task"])
            test_cmd = python_test_command()  # fixed: visitors can't run arbitrary commands
        else:
            repo, test_cmd = Path(project["path"]).expanduser(), project["test_cmd"]

        provider = create_provider(model["provider"], api_key, model["model"])
        workspace = Workspace(
            repo,
            test_cmd,
            test_timeout=HOSTED_TEST_TIMEOUT if hosted else 300,
            limit_resources=hosted,
        )
        with container:
            st.html(
                f'<div class="gl-h">Fixing {esc(project["label"])}</div>'
                f'<div class="gl-muted">{esc(PROVIDERS[model["provider"]].label)} · '
                f"<code>{esc(provider.model)}</code> · up to {model['max_steps']} steps</div>"
            )
            view = RunView(st.container(border=True))
        agent = Agent(
            provider,
            workspace,
            Budget(max_steps=model["max_steps"], max_cost_usd=model["max_cost"]),
            price=price_for(provider.model),
            on_event=view.on_event,
        )
        result = agent.run()
        view.finish(result.duration_s)
        st.session_state.last_report = to_report(result, project["label"])
        with container:
            render_report(st.session_state.last_report)
    except Exception as exc:  # Streamlit's stop/rerun signals are BaseException, not caught
        container.error("The run failed unexpectedly. Details:")
        container.text(redact(f"{type(exc).__name__}: {exc}"))
    finally:
        _RUN_SLOTS.release()
        forget_secret(api_key)
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def to_report(result: RunResult, label: str) -> dict[str, Any]:
    first = next((e for e in result.trace if e["type"] == "initial_tests"), None)
    return {
        "label": label,
        "status": result.status,
        "steps": result.steps,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "cost_usd": result.cost_usd,
        "duration_s": result.duration_s,
        "summary": result.summary,
        "diff": result.diff,
        "before": test_counts(first["output"]) if first else "",
        "after": test_counts(result.test_output),
        "test_output": result.test_output,
        "error": result.error,
    }


def render_report(report: dict[str, Any]) -> None:
    cls, icon, title = VERDICTS.get(report["status"], ("bad", "⚠️", report["status"]))
    before_after = ""
    if report["before"] and report["after"]:
        before_after = (
            f'<div class="ba">Tests: <span class="x">{esc(report["before"])}</span>'
            f' → <span class="{"y" if report["status"] == "fixed" else "x"}">'
            f"{esc(report['after'])}</span></div>"
        )
    why = f'<div class="why">{esc(report["error"])}</div>' if report["error"] else ""
    st.html(
        f'<div class="gl-verdict {cls}"><div class="v">{icon} {esc(title)}</div>'
        f"{before_after}{why}</div>"
    )

    cost = report["cost_usd"]
    cols = st.columns(4)
    cols[0].metric("Steps", report["steps"])
    cols[1].metric("Time", f"{report['duration_s']:.0f}s")
    cols[2].metric("Tokens", f"{report['input_tokens'] + report['output_tokens']:,}")
    cols[3].metric("Est. cost", "n/a" if cost is None else f"${cost:.4f}")

    tabs = st.tabs(["Changes", "Agent's explanation", "Final test output"])
    with tabs[0]:
        if report["diff"]:
            st.code(report["diff"], language="diff")
        else:
            st.caption("No files were changed.")
    with tabs[1]:
        if report["summary"]:
            st.text(report["summary"])
        else:
            st.caption("The agent didn't write a summary.")
    with tabs[2]:
        st.code(tail(report["test_output"], 60) or "(no output)", language=None)


def footer() -> None:
    st.html(
        '<div class="gl-footer">Greenlight is open source (MIT). '
        f'<a href="{REPO_URL}" target="_blank" rel="noopener">View the code on GitHub</a> · '
        "Runs with your own API key · Built with Claude Code</div>"
    )


def main() -> None:
    st.set_page_config(
        page_title="Greenlight: AI that fixes failing tests",
        page_icon="🟢",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    register_env_secrets()
    st.html(CSS)
    hero()

    left, right = st.columns([1.15, 1], gap="large")
    with left, st.container(border=True):
        project = choose_project()
    with right, st.container(border=True):
        model = connect_model()

    step_header(3, "Run the agent", "Watch it work live. A run usually takes under a minute.")
    clicked = st.button(
        "🟢  Find and fix the bug",
        type="primary",
        width="stretch",
        on_click=capture_key,
        args=(model["provider"],),
    )
    results = st.container()

    if clicked:
        # The key was moved out of the widget by capture_key; take it out of the session too.
        model["api_key"] = st.session_state.pop("_run_key", "")
        provider = PROVIDERS[model["provider"]]
        if project is None:
            results.error("Choose a project first.")
        elif not model["api_key"]:
            results.error(f"Paste your {provider.label} API key in step 2 to run the agent.")
        elif project["source"] == "path" and not Path(project["path"]).expanduser().is_dir():
            results.error("Enter the path of an existing folder in step 1.")
        else:
            run_agent(project, model, results)
    elif report := st.session_state.get("last_report"):
        with results:
            st.html(f'<div class="gl-h">Last run: {esc(report["label"])}</div>')
            render_report(report)

    footer()


if __name__ == "__main__":
    main()
