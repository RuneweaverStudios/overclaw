#!/usr/bin/env bash
# Create a Python 3.12 virtualenv and install buzz-captions for speech-to-text.
# Buzz requires Python >=3.12,<3.13 (see https://github.com/chidiwilliams/buzz).
# Run this once; then set "buzz_python" in config.json to the venv's python path.
#
# Prerequisites: Python 3.12 on PATH as python3.12 or python3 (if system is 3.12).
#   macOS: brew install python@3.12 && export PATH="/opt/homebrew/opt/python@3.12/bin:$PATH"
#   Or: pyenv install 3.12 && pyenv local 3.12 in this directory

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUZZ_VENV="${BUZZ_VENV:-$SCRIPT_DIR/buzz_venv}"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
CONFIG_JSON="$SCRIPT_DIR/../config.json"

# Prefer python3.12, then python3 if it reports 3.12
PYTHON=""
for py in python3.12 python3; do
  if command -v "$py" &>/dev/null; then
    ver=$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)
    if [[ "$ver" == "3.12" ]]; then
      PYTHON="$py"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "Error: Python 3.12 not found. Install it (e.g. brew install python@3.12 or pyenv install 3.12) and ensure it is on PATH."
  exit 1
fi

echo "Using $PYTHON: $($PYTHON --version)"
mkdir -p "$(dirname "$BUZZ_VENV")"
"$PYTHON" -m venv "$BUZZ_VENV"
"$BUZZ_VENV/bin/pip" install --upgrade pip
"$BUZZ_VENV/bin/pip" install buzz-captions

BUZZ_PYTHON="$BUZZ_VENV/bin/python3"
if [[ ! -f "$BUZZ_PYTHON" ]]; then
  BUZZ_PYTHON="$BUZZ_VENV/bin/python"
fi
echo "Buzz venv installed at $BUZZ_VENV"
echo "Python: $BUZZ_PYTHON"
"$BUZZ_PYTHON" -m buzz --version 2>/dev/null || "$BUZZ_PYTHON" -c "import buzz; print('buzz OK')"

if [[ -f "$CONFIG_JSON" ]]; then
  # Add or update buzz_python in config (requires jq or manual edit)
  if command -v jq &>/dev/null; then
    tmp=$(mktemp)
    jq --arg py "$BUZZ_PYTHON" '.buzz_python = $py | .use_buzz_for_recognition = true' "$CONFIG_JSON" > "$tmp" && mv "$tmp" "$CONFIG_JSON"
    echo "Updated config.json with buzz_python and use_buzz_for_recognition=true"
  else
    echo "Add to $CONFIG_JSON: \"buzz_python\": \"$BUZZ_PYTHON\", \"use_buzz_for_recognition\": true"
  fi
else
  echo "Add to your config.json: \"buzz_python\": \"$BUZZ_PYTHON\", \"use_buzz_for_recognition\": true"
fi
