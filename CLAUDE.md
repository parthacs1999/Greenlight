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
- `keys.py`: BYOK. Keys come from env vars, then the OS keychain (`keyring`). Never add a CLI flag that takes a key.

## Conventions

- Tests use `ScriptedProvider` (`tests/conftest.py`) for the agent and fake SDK clients (`tests/test_providers.py`) for the adapters. Never make real API calls in tests.
- Test subprocesses run with `PYTHONDONTWRITEBYTECODE=1`, because stale `.pyc` files otherwise hide same-second edits.
- `evals/tasks/*` are intentionally buggy repos. Don't fix them, and ruff excludes them. `run_eval.py` copies each one to a temp dir.
- Model IDs and prices live in `pricing.py` and the provider `DEFAULT_MODEL` constants.
- `.env` and `k.txt` hold real secrets. Don't read them.
