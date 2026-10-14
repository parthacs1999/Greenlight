"""Entry point for `streamlit run` and Streamlit Community Cloud.

Always load Greenlight from this checkout's src/ folder, never from an installed copy.
Streamlit Cloud installs requirements.txt once and only reinstalls when that file
changes, so after a `git push` an installed copy would keep running stale code while
the rest of the repo (e.g. evals/tasks) is new.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from greenlight.streamlit_app import main  # noqa: E402

main()
