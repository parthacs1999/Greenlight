from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from . import __version__
from .agent import Agent, Budget, RunResult
from .keys import (
    PROVIDER_ENV_VARS,
    MissingKeyError,
    delete_api_key,
    mask,
    resolve_api_key,
    store_api_key,
)
from .pricing import Price, price_for
from .providers import ProviderError, create_provider
from .security import redact, register_env_secrets
from .tools import Workspace

app = typer.Typer(
    help="Greenlight: an AI agent that fixes failing tests. Bring your own API key.",
    no_args_is_help=True,
    add_completion=False,
)
auth_app = typer.Typer(help="Manage API keys stored in your OS keychain.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")
console = Console()


class ProviderName(str, Enum):
    claude = "claude"
    gemini = "gemini"


def _short(value: object, limit: int = 80) -> str:
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _print_event(kind: str, data: dict) -> None:
    if kind == "model_turn":
        if data["text"].strip() and data["tool_calls"]:
            console.print(f"[dim italic]{_short(data['text'], 160)}[/]")
        for call in data["tool_calls"]:
            args = ", ".join(f"{k}={_short(v, 50)!s}" for k, v in call["args"].items())
            console.print(f"[cyan]→ {call['name']}[/]([dim]{args}[/])")
    elif kind == "tool_result":
        first = data["output"].strip().splitlines()[0] if data["output"].strip() else ""
        style = "red" if data["is_error"] else "green"
        console.print(f"  [{style}]{'✗' if data['is_error'] else '✓'}[/] [dim]{_short(first)}[/]")
    elif kind == "budget_stop":
        console.print(f"[yellow]Stopping: {data['reason']}[/]")
    elif kind == "error":
        console.print(f"[red]Provider error: {data['message']}[/]")


def _approve(rel: str, diff: str) -> bool:
    console.print(Syntax(diff or "(no changes)", "diff", theme="ansi_dark"))
    return typer.confirm(f"Apply this change to {rel}?", default=True)


def _save_trace(repo: Path, result: RunResult, meta: dict) -> Path:
    runs = repo / ".greenlight" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (repo / ".greenlight" / ".gitignore").write_text("*\n")  # never commit traces
    path = runs / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    payload = {
        **meta,
        "status": result.status,
        "steps": result.steps,
        "usage": vars(result.usage),
        "cost_usd": result.cost_usd,
        "duration_s": round(result.duration_s, 2),
        "summary": result.summary,
        "error": result.error,
        "diff": result.diff,
        "events": result.trace,
    }
    path.write_text(redact(json.dumps(payload, indent=2, default=str)))
    return path


@app.command()
def fix(
    repo: Annotated[Path, typer.Argument(help="Repository to fix.")] = Path("."),
    test_cmd: Annotated[str, typer.Option("--test-cmd", "-t", help="Test command.")] = "pytest -q",
    provider: Annotated[ProviderName, typer.Option("--provider", "-p")] = ProviderName.claude,
    model: Annotated[str | None, typer.Option(help="Model id (default per provider).")] = None,
    effort: Annotated[str, typer.Option(help="Claude effort: low|medium|high|xhigh|max.")] = "high",
    max_steps: Annotated[int, typer.Option(help="Max model turns.")] = 30,
    max_cost: Annotated[
        float, typer.Option(help="Stop once estimated cost (USD) hits this.")
    ] = 1.0,
    input_price: Annotated[
        float | None, typer.Option(help="Override input price, USD per 1M tokens.")
    ] = None,
    output_price: Annotated[
        float | None, typer.Option(help="Override output price, USD per 1M tokens.")
    ] = None,
    review: Annotated[bool, typer.Option(help="Ask before applying each file change.")] = False,
    allow_test_edits: Annotated[
        bool, typer.Option(help="Let the agent modify test files (off by default).")
    ] = False,
    trace: Annotated[bool, typer.Option(help="Save a JSON trace in .greenlight/runs/.")] = True,
) -> None:
    """Run the tests, and if they fail, let the agent find and fix the bug."""
    load_dotenv(find_dotenv(usecwd=True))
    register_env_secrets()

    try:
        api_key, key_source = resolve_api_key(provider.value)
    except MissingKeyError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc

    llm = create_provider(provider.value, api_key, model=model, effort=effort)
    price = (
        Price(input_price, output_price)
        if input_price is not None and output_price is not None
        else price_for(llm.model)
    )
    workspace = Workspace(
        repo, test_cmd, allow_test_edits=allow_test_edits, approve=_approve if review else None
    )

    console.print(
        Panel.fit(
            f"[bold]Greenlight[/] v{__version__}\n"
            f"repo      {workspace.root}\n"
            f"tests     {test_cmd}\n"
            f"model     {llm.name} / {llm.model}\n"
            f"api key   {mask(api_key)} from {key_source}\n"
            f"budget    {max_steps} steps"
            + (f", ${max_cost:.2f}" if price else " (no price known for this model: no cost cap)"),
            border_style="green",
        )
    )

    agent = Agent(
        llm,
        workspace,
        Budget(max_steps=max_steps, max_cost_usd=max_cost),
        price=price,
        on_event=_print_event,
    )
    console.print("[dim]Running tests...[/]")
    try:
        result = agent.run()
    except ProviderError as exc:  # pragma: no cover - agent.run converts these
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc

    _report(result)
    if trace and result.status != "already_passing":
        meta = {"provider": llm.name, "model": llm.model, "test_cmd": test_cmd}
        path = _save_trace(workspace.root, result, meta)
        console.print(f"[dim]Trace saved to {path}[/]")
    raise typer.Exit(0 if result.success else 1)


def _report(result: RunResult) -> None:
    if result.status == "already_passing":
        console.print("[green]✓ Tests already pass. Nothing to fix.[/]")
        return
    if result.summary:
        console.print(Panel(result.summary.strip(), title="Agent summary", border_style="blue"))
    if result.diff:
        console.print(Syntax(result.diff, "diff", theme="ansi_dark"))

    colours = {"fixed": "green", "not_fixed": "red", "budget_exceeded": "yellow", "error": "red"}
    table = Table(show_header=False, box=None)
    table.add_row("Result", f"[bold {colours[result.status]}]{result.status.upper()}[/]")
    table.add_row("Verified by", "re-running the test command independently")
    table.add_row("Steps", str(result.steps))
    table.add_row(
        "Tokens",
        f"{result.usage.input_tokens:,} in ({result.usage.cached_input_tokens:,} cached) / "
        f"{result.usage.output_tokens:,} out",
    )
    cost = f"${result.cost_usd:.4f}" if result.cost_usd is not None else "unknown"
    table.add_row("Est. cost", cost)
    table.add_row("Time", f"{result.duration_s:.1f}s")
    if result.error:
        table.add_row("Reason", result.error)
    console.print(Panel(table, title="Run report", border_style=colours[result.status]))
    if result.status != "fixed":
        console.print(Panel(result.test_output[-3000:], title="Final test output"))


@app.command()
def ui(
    port: Annotated[int, typer.Option(help="Port to serve on.")] = 8501,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
) -> None:
    """Open the web UI on this machine (localhost only), with local folders and keys enabled."""
    try:
        import streamlit  # noqa: F401
    except ImportError as exc:
        console.print('[red]The UI needs Streamlit: pip install -e ".[ui]"[/]')
        raise typer.Exit(2) from exc

    app_path = Path(__file__).with_name("streamlit_app.py")
    command = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.address", "127.0.0.1",
        # Reject WebSocket connections with any other Host header, so a malicious site
        # can't drive this server via DNS rebinding and use local-folder mode to run
        # commands. (List options take one flag per value.)
        "--server.allowedHosts", "localhost",
        "--server.allowedHosts", "127.0.0.1",
        "--server.port", str(port),
        "--server.headless", "false" if open_browser else "true",
        "--browser.gatherUsageStats", "false",
    ]  # fmt: skip
    env = {**os.environ, "GREENLIGHT_LOCAL": "1"}
    raise typer.Exit(subprocess.call(command, env=env))


@auth_app.command("set")
def auth_set(provider: ProviderName) -> None:
    """Store an API key in your OS keychain (input is hidden)."""
    key = typer.prompt(f"{provider.value} API key", hide_input=True)
    store_api_key(provider.value, key)
    console.print(f"[green]Saved {mask(key)} to the OS keychain for {provider.value}.[/]")


@auth_app.command("status")
def auth_status() -> None:
    """Show where each provider's key would be loaded from (keys are masked)."""
    load_dotenv(find_dotenv(usecwd=True))
    for name in PROVIDER_ENV_VARS:
        try:
            key, source = resolve_api_key(name)
            console.print(f"{name:<7} [green]{mask(key)}[/] from {source}")
        except MissingKeyError:
            console.print(f"{name:<7} [yellow]not configured[/]")


@auth_app.command("clear")
def auth_clear(provider: ProviderName) -> None:
    """Remove a stored key from the OS keychain."""
    if delete_api_key(provider.value):
        console.print(f"Removed the {provider.value} key from the keychain.")
    else:
        console.print(f"No {provider.value} key was stored in the keychain.")


if __name__ == "__main__":
    app()
