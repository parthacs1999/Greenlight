from typer.testing import CliRunner

from greenlight import cli


def test_ui_binds_locally_and_restricts_hosts(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd, env: calls.append((cmd, env)) or 0)
    result = CliRunner().invoke(cli.app, ["ui", "--no-open"])
    assert result.exit_code == 0, result.output

    cmd, env = calls[0]
    assert cmd[cmd.index("--server.address") + 1] == "127.0.0.1"
    # Streamlit list options take one flag per value; a comma-joined string matches no host.
    hosts = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--server.allowedHosts"]
    assert hosts == ["localhost", "127.0.0.1"]
    assert env["GREENLIGHT_LOCAL"] == "1"
