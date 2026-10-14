import os

import pytest

from greenlight import security
from greenlight.security import PathDenied, redact, resolve_in_workspace, scrubbed_env


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "sub/../../x"])
def test_paths_outside_workspace_are_denied(repo, path):
    with pytest.raises(PathDenied, match="outside"):
        resolve_in_workspace(repo, path)


def test_symlink_escape_is_denied(repo, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    (repo / "link.txt").symlink_to(secret)
    with pytest.raises(PathDenied):
        resolve_in_workspace(repo, "link.txt")


@pytest.mark.parametrize("path", [".env", ".env.local", "config/server.pem", "id_rsa"])
def test_secret_files_are_denied(repo, path):
    with pytest.raises(PathDenied, match="secret"):
        resolve_in_workspace(repo, path)


def test_git_dir_is_denied(repo):
    with pytest.raises(PathDenied, match="protected"):
        resolve_in_workspace(repo, ".git/config")


def test_normal_file_is_allowed(repo):
    assert resolve_in_workspace(repo, "calc.py") == (repo / "calc.py").resolve()


def test_extra_deny_patterns(repo):
    with pytest.raises(PathDenied):
        resolve_in_workspace(repo, "k.txt", extra_deny=("k.txt",))


def test_scrubbed_env_removes_credentials(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("MY_DB_PASSWORD", "p")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("HARMLESS_SETTING", "ok")
    env = scrubbed_env()
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "MY_DB_PASSWORD", "GITHUB_TOKEN"):
        assert name not in env
    assert env["HARMLESS_SETTING"] == "ok"
    assert env["PATH"] == os.environ["PATH"]


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-" + "api03-" + "x" * 30,
        "AIza" + "a" * 35,
        "ghp_" + "b" * 36,
        "AKIA" + "Z" * 16,
    ],
)
def test_redact_known_key_formats(secret):
    assert secret not in redact(f"value={secret} end")


def test_redact_registered_secret():
    security.register_secret("custom-secret-value-42")
    assert redact("token custom-secret-value-42 here") == "token [REDACTED] here"


def test_forget_secret_stops_retaining_it():
    security.register_secret("per-run-visitor-key-77")
    security.forget_secret("per-run-visitor-key-77")
    assert "per-run-visitor-key-77" not in security._known_secrets


def test_redact_leaves_normal_text():
    text = "def add(a, b):\n    return a + b"
    assert redact(text) == text
