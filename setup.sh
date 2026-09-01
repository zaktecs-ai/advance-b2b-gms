#!/usr/bin/env bash
# Idempotent one-command setup for Ubuntu 22.04 / 24.04.
#   apt deps -> venv -> pip -> playwright chromium + system deps
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR=".venv"

echo "==> Installing system dependencies (apt)"
sudo apt-get update -y
# A failure here MUST stop the script (set -e) rather than silently degrade
# the runtime (E1). Each optional package lives on its own line with a loud,
# non-fatal warning instead of muting the whole install.
sudo apt-get install -y \
  "$PYTHON_BIN" "$PYTHON_BIN-venv" "$PYTHON_BIN-dev" \
  git tmux \
  libjpeg-dev zlib1g-dev libssl-dev libffi-dev \
  libxml2-dev libxslt1-dev
# tmux is genuinely optional (server.sh falls back to nohup); warn only.
if ! command -v tmux >/dev/null 2>&1; then
  echo "[warn] tmux is not installed; './server.sh run' will use nohup instead." >&2
fi

echo "==> Creating virtualenv"
if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

echo "==> Installing Python dependencies"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Installing Playwright Chromium + system deps"
python -m playwright install --with-deps chromium

echo ""
echo "Setup complete. Try it:"
echo "  ./run.sh --demo          # offline test (no browser needed)"
echo "  ./run.sh                 # live scrape"
