"""The Streamlit UI, driven headlessly with AppTest and a scripted model (no network)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import greenlight.streamlit_app as ui
from greenlight import security
from greenlight.providers.base import ToolCall, Turn, Usage

from .conftest import ScriptedProvider

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "streamlit_app.py")
VISITOR_KEY = "AIza" + "-visitor-key-" + "1234567890"  # fake; split so scanners skip it
PAYLOAD = 'Checking <img src=x onerror="alert(1)"> first'


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("GEMINI_API_KEY", "server-side-key-must-not-be-used")
    monkeypatch.delenv("GREENLIGHT_LOCAL", raising=False)


def page_html(at: AppTest) -> str:
    return " ".join(str(el.proto.body) for el in at.get("html"))


def fixing_provider():
    edit = {"path": "pricing.py", "old_str": "* percent,", "new_str": "* percent / 100,"}
    return ScriptedProvider(
        [
            Turn(PAYLOAD, [ToolCall("c0", "read_file", {"path": ".env"})], Usage(80, 0, 10)),
            Turn("", [ToolCall("c1", "edit_file", edit)], Usage(100, 0, 20)),
            Turn("Root cause: <b>percent</b> was used as a fraction.", [], Usage(150, 100, 30)),
        ]
    )


def test_landing_page_explains_the_product_and_key_handling():
    at = AppTest.from_file(APP).run()
    assert not at.exception
    html = page_html(at)
    assert "finds the bug and fixes it" in html
    assert "used for this one run and then discarded" in html
    assert "Tests right now:" in html and "failed" in html  # live preview of the bug
    assert any("How your key is protected" in e.label for e in at.expander)


def test_hosted_mode_is_demo_only():
    at = AppTest.from_file(APP).run()
    assert all(sc.key != "source" for sc in at.get("button_group"))


@pytest.mark.parametrize("local", [False, True])
def test_never_uses_server_keys(monkeypatch, local):
    if local:
        monkeypatch.setenv("GREENLIGHT_LOCAL", "1")
    called = []
    monkeypatch.setattr(ui, "create_provider", lambda *a, **k: called.append(a))
    at = AppTest.from_file(APP).run()
    at.button[0].click().run()
    assert any("Paste your Gemini API key" in e.value for e in at.error)
    assert not called


def test_local_mode_offers_own_folder(monkeypatch):
    monkeypatch.setenv("GREENLIGHT_LOCAL", "1")
    at = AppTest.from_file(APP).run()
    assert any(sc.key == "source" for sc in at.get("button_group"))


def test_demo_run_end_to_end(monkeypatch):
    seen_keys = []

    def fake_create_provider(name, api_key, model):
        seen_keys.append(api_key)
        return fixing_provider()

    monkeypatch.setattr(ui, "create_provider", fake_create_provider)
    original = (ROOT / "evals/tasks/cross_module_discount/pricing.py").read_text()

    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_input(key="api_key_gemini").input(VISITOR_KEY)
    at.button[0].click().run()

    assert not at.exception
    assert seen_keys == [VISITOR_KEY]
    html = page_html(at)
    assert "Fixed and verified" in html
    assert "1 failed, 3 passed" in html and "4 passed" in html  # before -> after
    assert "blocked" in html  # the .env read attempt is shown as a guardrail
    # Model text shown in the feed is escaped, never rendered as HTML.
    assert "&lt;img src=x" in html and "<img src=x" not in html
    assert any("/ 100" in c.value for c in at.code)
    # The bundled project is untouched, and the key is gone from session and memory.
    assert (ROOT / "evals/tasks/cross_module_discount/pricing.py").read_text() == original
    assert at.text_input(key="api_key_gemini").value == ""  # field blanked via callback
    assert "_run_key" not in at.session_state
    assert VISITOR_KEY not in security._known_secrets


def test_concurrent_run_limit(monkeypatch):
    called = []
    monkeypatch.setattr(ui, "create_provider", lambda *a, **k: called.append(a))
    monkeypatch.setattr(ui, "_RUN_SLOTS", ui.threading.BoundedSemaphore(1))
    ui._RUN_SLOTS.acquire()  # another visitor's run is in progress
    try:
        at = AppTest.from_file(APP).run()
        at.text_input(key="api_key_gemini").input(VISITOR_KEY)
        at.button[0].click().run()
        assert any("busy" in w.value for w in at.warning)
        assert not called
    finally:
        ui._RUN_SLOTS.release()
