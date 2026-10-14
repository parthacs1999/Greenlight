# Records docs/demo.gif with https://github.com/charmbracelet/vhs
# Run from the repo root:  vhs docs/demo.tape
# Makes real API calls with your own key (loaded from .env in the hidden setup).

Output docs/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1280
Set Height 820
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set WindowBar Colorful

Hide
Type "export REPO=$PWD PATH=$PWD/.venv/bin:$PATH COLUMNS=110 PS1='$ '" Enter
Type `export GEMINI_API_KEY="$(python -c 'from dotenv import dotenv_values; print(dotenv_values(".env")["GEMINI_API_KEY"])')"` Enter
Type "rm -rf /tmp/shop && cp -r evals/tasks/cross_module_discount /tmp/shop && cd /tmp" Enter
Type "clear" Enter
Show

Type "# A shopping-cart project with a failing test:" Enter
Sleep 600ms
Type "python -m pytest -q shop 2>&1 | tail -3" Enter
Sleep 2.5s

Type "# Let Greenlight find and fix the bug, using my own Gemini key:" Enter
Sleep 600ms
Type "greenlight fix shop -p gemini --model gemini-3.1-flash-lite -t 'python -m pytest -q'" Sleep 800ms Enter
# A run takes ~25s; give it room, then hold on the final report.
Sleep 45s
Sleep 4s
