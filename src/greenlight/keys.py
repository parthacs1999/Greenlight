"""Bring-your-own-key handling.

Keys are resolved on the user's machine, in this order:
1. Environment variables (including a git-ignored `.env`, loaded by the CLI).
2. The OS keychain (macOS Keychain, Windows Credential Manager, Secret Service),
   populated with `greenlight auth set <provider>`.

Keys are never accepted as CLI arguments (they'd end up in shell history), never
written to disk by Greenlight, and only ever sent to the provider's own API.
"""

from __future__ import annotations

import os

from .security import register_secret

KEYCHAIN_SERVICE = "greenlight"

PROVIDER_ENV_VARS: dict[str, tuple[str, ...]] = {
    "claude": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


class MissingKeyError(Exception):
    pass


def _keyring():
    try:
        import keyring

        return keyring
    except ImportError:  # pragma: no cover - keyring is a declared dependency
        return None


def resolve_api_key(provider: str) -> tuple[str, str]:
    """Return (key, source description). Raises MissingKeyError with setup instructions."""
    env_vars = PROVIDER_ENV_VARS[provider]
    for name in env_vars:
        if value := os.environ.get(name, "").strip():
            register_secret(value)
            return value, f"environment (${name})"

    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(KEYCHAIN_SERVICE, provider)
        except Exception:  # no keychain backend available (e.g. headless CI)
            value = None
        if value:
            register_secret(value)
            return value, "OS keychain"

    raise MissingKeyError(
        f"No API key found for {provider}.\n"
        f"  - export {env_vars[0]}=...  (or add it to a git-ignored .env file), or\n"
        f"  - run `greenlight auth set {provider}` to store it in your OS keychain."
    )


def store_api_key(provider: str, key: str) -> None:
    kr = _keyring()
    if kr is None:
        raise RuntimeError("keyring is not installed")
    kr.set_password(KEYCHAIN_SERVICE, provider, key.strip())


def delete_api_key(provider: str) -> bool:
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.delete_password(KEYCHAIN_SERVICE, provider)
        return True
    except Exception:
        return False


def mask(key: str) -> str:
    return f"****{key[-4:]}" if len(key) > 8 else "****"
