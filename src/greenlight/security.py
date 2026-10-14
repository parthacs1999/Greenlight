"""Guardrails that keep the agent, and the LLM behind it, away from secrets.

Three layers:
1. Path confinement: tools can only touch files inside the workspace, never `.git`
   and never secret-looking files (.env, private keys, ...).
2. Environment scrubbing: subprocesses (the test command) run without API keys or
   other credentials in their environment, so test code can't read them.
3. Redaction: anything key-shaped is masked before it reaches the model, the
   terminal or the trace file.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

SECRET_FILE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.*",
    "*.keystore",
)

# Directories the agent may never read or write.
FORBIDDEN_DIRS = frozenset({".git", ".greenlight"})

# Directories that are skipped when listing or searching (noise, not secrets).
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".greenlight",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".tox",
    }
)

_SENSITIVE_ENV_NAME = re.compile(
    r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION", re.IGNORECASE
)
_SENSITIVE_ENV_PREFIXES = (
    "ANTHROPIC_",
    "GEMINI_",
    "GOOGLE_",
    "OPENAI_",
    "AWS_",
    "AZURE_",
    "GH_",
    "GITHUB_",
)

_KEY_PATTERNS = [
    re.compile(p)
    for p in (
        r"sk-ant-[A-Za-z0-9_\-]{10,}",  # Anthropic
        r"AIza[0-9A-Za-z_\-]{35}",  # Google API key
        r"AQ\.[A-Za-z0-9_\-]{20,}",  # Google access key
        r"gh[pousr]_[A-Za-z0-9]{30,}",  # GitHub
        r"AKIA[0-9A-Z]{16}",  # AWS access key id
        r"sk-[A-Za-z0-9_\-]{20,}",  # OpenAI-style
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    )
]

REDACTED = "[REDACTED]"
_known_secrets: set[str] = set()


class PathDenied(Exception):
    """Raised when a tool tries to touch a path it isn't allowed to."""


def is_secret_file(name: str, extra_patterns: tuple[str, ...] = ()) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in (*SECRET_FILE_PATTERNS, *extra_patterns))


def resolve_in_workspace(root: Path, relative: str, extra_deny: tuple[str, ...] = ()) -> Path:
    """Resolve `relative` against `root`, refusing anything unsafe.

    Symlinks are resolved first, so a link pointing outside the workspace is caught.
    """
    root = root.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise PathDenied(f"'{relative}' is outside the workspace")
    parts = target.relative_to(root).parts
    if any(part in FORBIDDEN_DIRS for part in parts):
        raise PathDenied(f"'{relative}' is in a protected directory")
    if any(is_secret_file(part, extra_deny) for part in parts):
        raise PathDenied(f"'{relative}' looks like a secret file and is off-limits")
    return target


def scrubbed_env() -> dict[str, str]:
    """A copy of os.environ without anything that looks like a credential."""
    return {
        name: value
        for name, value in os.environ.items()
        if not _SENSITIVE_ENV_NAME.search(name) and not name.startswith(_SENSITIVE_ENV_PREFIXES)
    }


def register_secret(value: str | None) -> None:
    """Remember a secret value (e.g. the resolved API key) so it's always redacted."""
    if value and len(value) >= 8:
        _known_secrets.add(value)


def forget_secret(value: str | None) -> None:
    """Drop a secret registered for one run, so a long-lived server doesn't retain it."""
    if value:
        _known_secrets.discard(value)


def register_env_secrets() -> None:
    for name, value in os.environ.items():
        if _SENSITIVE_ENV_NAME.search(name) or name.startswith(_SENSITIVE_ENV_PREFIXES):
            register_secret(value)


def redact(text: str) -> str:
    for secret in sorted(_known_secrets, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    for pattern in _KEY_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text
