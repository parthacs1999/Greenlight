import pytest

from greenlight import keys
from greenlight.keys import MissingKeyError, mask, resolve_api_key


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, value):
        self.store[(service, user)] = value

    def delete_password(self, service, user):
        del self.store[(service, user)]


@pytest.fixture
def keyring(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(keys, "_keyring", lambda: fake)
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return fake


def test_env_var_wins(keyring, monkeypatch):
    keyring.set_password("greenlight", "claude", "from-keychain-123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env-456")
    assert resolve_api_key("claude") == ("from-env-456", "environment ($ANTHROPIC_API_KEY)")


def test_falls_back_to_keychain(keyring):
    keys.store_api_key("gemini", "  gem-key-789  ")
    assert resolve_api_key("gemini") == ("gem-key-789", "OS keychain")


def test_gemini_accepts_google_api_key(keyring, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key-000")
    assert resolve_api_key("gemini")[0] == "google-key-000"


def test_missing_key_explains_setup(keyring):
    with pytest.raises(MissingKeyError, match="(?s)ANTHROPIC_API_KEY.*greenlight auth set claude"):
        resolve_api_key("claude")


def test_delete(keyring):
    keys.store_api_key("claude", "k" * 20)
    assert keys.delete_api_key("claude")
    assert not keys.delete_api_key("claude")


def test_mask_never_shows_whole_key():
    assert mask("sk-ant-1234567890abcd") == "****abcd"
    assert mask("short") == "****"
