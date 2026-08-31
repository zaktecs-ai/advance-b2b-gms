#!/usr/bin/env bash
# One-command launcher. Handles the venv + working dir for you.
#
#   ./run.sh --demo          -> offline test (sample records, no browser)
#   ./run.sh                 -> live scrape (config.yaml + .env)
#   ./run.sh --config other.yaml
set -euo pipefail

cd "$(dirname "$0")"

# Activate the venv if present; otherwise choose the system's Python 3.
PYTHON_CMD="python3"
if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
  PYTHON_CMD="python"
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD="python"
fi

exec "$PYTHON_CMD" -m scraper.main "$@"
