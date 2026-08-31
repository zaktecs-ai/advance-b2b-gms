#!/usr/bin/env bash
# One-command launcher. Handles the venv + working dir for you.
#
#   ./run.sh --demo          -> offline test (sample records, no browser)
#   ./run.sh                 -> live scrape (config.yaml + .env)
#   ./run.sh --config other.yaml
set -euo pipefail

cd "$(dirname "$0")"

# Activate the venv if present; otherwise rely on the system python.
if [ -x ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

exec python -m scraper.main "$@"
