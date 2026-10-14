# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Greenlight: a CLI agent that fixes failing tests. It runs the user's test command, gives the failure to an LLM (Claude or Gemini, using the user's own API key), lets the model call guarded tools to explore and edit the repo, then re-runs the tests itself to decide the verdict.

## Commands

The venv is at `.venv/` (Python 3.13; the package supports 3.10+).

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q                                    # full suite: no API key or network
.venv/bin/python -m pytest tests/test_tools.py::test_edit_file_replaces_unique_match
.venv/bin/ruff check . && .venv/bin/ruff format --check .        # CI runs both
.venv/bin/greenlight fix <repo> -p gemini -t "<abs path>/python -m pytest -q"
.venv/bin/greenlight ui                                          # web UI on 127.0.0.1:8501
.venv/bin/python evals/run_eval.py --provider claude             # spends the user's API credits
```

Pass the test command with an absolute interpreter path. It runs with `cwd` set to the target repo, so a relative `.venv/bin/python` won't be found.

## Architecture

- `agent.py`: the loop. Initial test run → `provider.start` → repeat `provider.step()`, `workspace.execute()` for each tool call, `provider.add_tool_results()` → stop on no tool calls or budget → **independent re-run of the tests** decides `fixed`/`not_fixed`. Exit code 127 (test command not found) aborts before any model call.
- `providers/`: the `Provider` ABC (`start`/`step`/`add_tool_results`). Each adapter keeps history in its SDK's **native** format and appends model turns unchanged (Claude thinking blocks, Gemini thought signatures). Don't convert history to a common format.
  - Claude: manual loop on `messages.create`. Models in `FALLBACK_MODELS` go through `client.beta.messages.create` with `fallbacks="default"`. Top-level `cache_control` caches the history. Forced `tool_choice` is a 400 on current models; don't add it.
  - Gemini: `automatic_function_calling` is disabled on purpose; SDK retries are set through `HttpRetryOptions`.
- `tools.py`: `Workspace` binds the tools to one repo. Each tool is a `Tool(name, description, JSON-schema parameters, handler)`, and both providers' tool schemas are generated from that list. Handlers raise `ToolError`, which `execute()` turns into `(message, is_error=True)` for the model. There is deliberately no shell tool; `run_tests` only runs the user's command plus an optional validated path target.
- `security.py`: every path goes through `resolve_in_workspace` (confinement, `.git`, secret files). Subprocesses get `scrubbed_env()`. Every tool output, trace and summary goes through `redact()`.
- `streamlit_app.py`: the web UI (`greenlight ui`, or `streamlit run streamlit_app.py` from the repo root, which is the Streamlit Cloud entry point). The UI **never** reads keys from env or the keychain: users always paste their own, and `run_agent` calls `forget_secret` when the run ends. **Hosted mode is the default.** Only `GREENLIGHT_LOCAL=1` (set by `greenlight ui`) enables local folders and a custom test command. Repo- or model-derived values must go through `esc()` before `st.html`, or use `st.text`/`st.code`. Never pass them to `st.markdown`. Tested headlessly in `tests/test_ui.py` with `AppTest`.
- `action.yml` (composite GitHub Action) installs the package, runs `greenlight fix`, then `python -m greenlight.github_report <dir> <exit-code>` reads the newest `.greenlight/runs/*.json` trace and writes `$GITHUB_OUTPUT`, `$GITHUB_STEP_SUMMARY` and `.greenlight/pr-body.md`. Pass action inputs to scripts via `env:`, never `${{ }}` inside `run:`. `examples/greenlight-on-ci-failure.yml` is the user-facing template; `.github/workflows/greenlight-demo.yml` is a manual demo that needs a `GEMINI_API_KEY` secret.
- `keys.py`: BYOK. Keys come from env vars, then the OS keychain (`keyring`). Never add a CLI flag that takes a key.

## Conventions

- Tests use `ScriptedProvider` (`tests/conftest.py`) for the agent and fake SDK clients (`tests/test_providers.py`) for the adapters. Never make real API calls in tests.
- Test subprocesses run with `PYTHONDONTWRITEBYTECODE=1`, because stale `.pyc` files otherwise hide same-second edits.
- `evals/tasks/*` are intentionally buggy repos. Don't fix them, and ruff excludes them. `run_eval.py` copies each one to a temp dir. `evals/hidden/<task>/` holds held-out tests that are copied in only after the agent finishes. They must never sit inside `evals/tasks/`, or the agent (and the web UI demo) would see them. A new task needs: a buggy repo with visible tests, hidden tests, and a `TASKS` entry in `run_eval.py` (plus a `DEMO_TASKS` entry in `streamlit_app.py` for the UI). Before adding it, check that the buggy version fails, a reference fix passes visible + hidden, and a hard-coded fix is caught by the hidden tests.
- Model IDs and prices live in `pricing.py` and the provider `DEFAULT_MODEL` constants.
- `evals/realworld/`: the real-world benchmark (SWE-bench style). `mine.py` scans repos' git history for small bug-fix commits and validates them (FAIL_TO_PASS/PASS_TO_PASS) into `manifest.json`; `run.py` runs a model (`--oracle` checks the scorer with the real fixes, `--resume` re-runs only quota/overload failures, `--rescore` rebuilds the summary); `compare.py` writes the leaderboard. Clones and per-repo venvs live in `~/.cache/greenlight-evals`; venvs hold dependencies only, and tests import the code from the workspace via `env PYTHONPATH=<src_root>`. Quota/overload errors (429/503) count as *not attempted*, never as failures.
- `docs/record_ui_demo.py` re-records `docs/ui-demo.{mp4,gif}` with Playwright against a hosted-mode server, and refuses to save unless the run ends "Fixed and verified".
- Replay mode: `src/greenlight/replays/<task>.json` holds recorded real runs (events and report) that the UI plays back without a key or AI calls. Re-record with `python scripts/record_replays.py` (it saves only runs that end fixed, scrubs temp paths, and falls back across lite models on quota errors). `tests/test_replays.py` checks every replay's diff passes the task's hidden tests, and `tests/test_ui.py` checks every demo project has one. Re-record after changing a demo task.
- The root `streamlit_app.py` puts `src/` first on `sys.path` on purpose. Streamlit Cloud installs `requirements.txt` once and doesn't reinstall on push, so without that the live app would keep running a stale installed copy while repo files like `evals/tasks/` are new.
- `.env` and `k.txt` hold real secrets. Don't read them.
