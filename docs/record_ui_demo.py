"""Record the web-UI demo video (docs/ui-demo.mp4 and docs/ui-demo.gif).

Starts the Streamlit app in hosted mode (what public visitors see), drives it in a real
browser with Playwright, and records a real run: pick a broken project, peek at the
code, paste a key, watch the agent fix the bug, read the result.

The API key is read from GEMINI_API_KEY or the repo's .env and typed into the password
field, so the video only ever shows dots.

Requires: pip install playwright && python -m playwright install chromium, plus ffmpeg.
Usage:    python docs/record_ui_demo.py   [DEMO_MODEL=gemini-3.1-flash-lite]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from dotenv import dotenv_values
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
PORT = 8502
URL = f"http://127.0.0.1:{PORT}"
MODEL = os.environ.get("DEMO_MODEL", "gemini-3.1-flash-lite")
SIZE = {"width": 1280, "height": 820}

# A visible cursor (Playwright's recordings don't show the mouse) that pulses on clicks.
CURSOR_JS = """
window.addEventListener('DOMContentLoaded', () => {
  const dot = document.createElement('div');
  dot.style.cssText = 'position:fixed;z-index:2147483647;width:22px;height:22px;margin:-11px 0 0 -11px;' +
    'border-radius:50%;background:rgba(22,163,74,.35);border:2px solid #16a34a;pointer-events:none;' +
    'transition:transform .15s ease;left:-50px;top:-50px';
  document.body.appendChild(dot);
  document.addEventListener('mousemove', e => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px'; }, true);
  document.addEventListener('mousedown', () => { dot.style.transform = 'scale(.6)'; }, true);
  document.addEventListener('mouseup', () => { dot.style.transform = 'scale(1)'; }, true);
});
"""


def api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or dotenv_values(ROOT / ".env").get("GEMINI_API_KEY")
    if not key:
        sys.exit("Set GEMINI_API_KEY (or put it in .env) to record the demo.")
    return key


def start_server() -> subprocess.Popen:
    # Hosted mode, with no API keys in the server's environment.
    env = {k: v for k, v in os.environ.items() if "API_KEY" not in k and k != "GREENLIGHT_LOCAL"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "streamlit_app.py",
         "--server.port", str(PORT), "--server.headless", "true",
         "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    for _ in range(60):
        try:
            if urllib.request.urlopen(f"{URL}/_stcore/health", timeout=1).read() == b"ok":
                return proc
        except OSError:
            time.sleep(0.5)
    proc.kill()
    sys.exit("The Streamlit server didn't start.")


def glide_click(page: Page, locator, pause: int = 350) -> None:
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=25)
    page.wait_for_timeout(pause)
    locator.click()


def scroll(page: Page, dy: int, steps: int = 25) -> None:
    page.mouse.move(SIZE["width"] / 2, SIZE["height"] / 2)
    for _ in range(steps):
        page.mouse.wheel(0, dy / steps)
        page.wait_for_timeout(25)


def record(video_dir: Path, key: str) -> Path:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport=SIZE, record_video_dir=str(video_dir), record_video_size=SIZE
        )
        context.add_init_script(CURSOR_JS)
        page = context.new_page()
        page.goto(URL)
        page.get_by_text("finds the bug and fixes it").wait_for(timeout=60_000)
        page.get_by_text("Tests right now").wait_for(timeout=60_000)
        # Streamlit renders top to bottom: wait for the last element before interacting.
        page.get_by_text("Find and fix the bug").wait_for(timeout=60_000)
        page.wait_for_timeout(2500)

        # Step 1: the broken project and a peek at the code.
        scroll(page, 380)
        page.wait_for_timeout(1200)
        glide_click(page, page.get_by_text("Shopping cart", exact=True))
        page.wait_for_timeout(1500)
        glide_click(page, page.get_by_text("Peek at the code and the failing tests"))
        page.wait_for_timeout(1500)
        glide_click(page, page.get_by_role("tab", name="pricing.py"))
        page.wait_for_timeout(2500)
        glide_click(page, page.get_by_text("Peek at the code and the failing tests"))
        page.wait_for_timeout(800)

        # Step 2: connect the model with your own key (shown as dots only).
        key_box = page.get_by_label("Gemini API key")
        glide_click(page, key_box)
        key_box.press_sequentially(key, delay=12)
        page.wait_for_timeout(800)
        glide_click(page, page.get_by_text("How your key is protected"))
        page.wait_for_timeout(4000)
        glide_click(page, page.get_by_text("How your key is protected"))
        page.wait_for_timeout(600)
        glide_click(page, page.get_by_text("Advanced: model and budget"))
        model_box = page.get_by_label("Model", exact=True)
        glide_click(page, model_box)
        model_box.fill(MODEL)
        model_box.press("Tab")
        page.wait_for_timeout(1200)

        # Step 3: run it and follow the live feed.
        glide_click(page, page.get_by_role("button", name="Find and fix the bug"), pause=600)
        verdicts = (
            page.get_by_text("Fixed and verified")
            .or_(page.get_by_text("Not fixed"))
            .or_(page.get_by_text("couldn't complete"))
        )
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline and not verdicts.first.is_visible():
            scroll(page, 140, steps=10)
            page.wait_for_timeout(700)
        verdicts.first.wait_for(timeout=10_000)
        verdicts.first.scroll_into_view_if_needed()
        page.wait_for_timeout(3500)

        # The result: verdict, diff, explanation.
        scroll(page, 250)
        page.wait_for_timeout(2500)
        glide_click(page, page.get_by_role("tab", name="Agent's explanation"))
        page.wait_for_timeout(4000)
        glide_click(page, page.get_by_role("tab", name="Changes"))
        page.wait_for_timeout(3500)

        fixed = page.get_by_text("Fixed and verified").is_visible()
        video = Path(page.video.path())
        context.close()
        browser.close()
        if not fixed:
            sys.exit("The run didn't end in 'Fixed and verified', so no video was saved. Retry.")
        return video


def convert(webm: Path) -> None:
    mp4, gif = DOCS / "ui-demo.mp4", DOCS / "ui-demo.gif"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-c:v", "libx264",
         "-preset", "slow", "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         str(mp4)],
        check=True,
    )  # fmt: skip
    palette = webm.with_suffix(".png")
    scale = "setpts=PTS/1.4,fps=8,scale=880:-1:flags=lanczos"  # GIF: 1.4x speed, smaller
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm),
         "-vf", f"{scale},palettegen=max_colors=128", str(palette)],
        check=True,
    )  # fmt: skip
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-i", str(palette),
         "-lavfi", f"{scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4", str(gif)],
        check=True,
    )  # fmt: skip
    for path in (mp4, gif):
        print(f"{path.relative_to(ROOT)}: {path.stat().st_size / 1e6:.1f} MB")


def main() -> int:
    key = api_key()
    server = start_server()
    video_dir = Path(tempfile.mkdtemp(prefix="greenlight-video-"))
    try:
        webm = record(video_dir, key)
        convert(webm)
    finally:
        server.terminate()
        shutil.rmtree(video_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
