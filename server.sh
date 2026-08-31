#!/usr/bin/env bash
# Simple server controller for Advance B2B GMS.
# Daily use:
#   ./server.sh setup   # install the project + Playwright (first time)
#   ./server.sh run     # start the scraper in tmux and survive SSH logout
#   ./server.sh update  # download the latest GitHub code
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

CONFIG_FILE="${ABGMS_CONFIG:-$ROOT_DIR/config.local.yaml}"
OUTPUT_DIR="${ABGMS_OUTPUT_DIR:-$ROOT_DIR/output}"
CLIENT_NAME="${ABGMS_CLIENT_NAME:-}"
SESSION_NAME="${ABGMS_TMUX_SESSION:-abgms}"
PID_FILE="$ROOT_DIR/.abgms.pid"
CONSOLE_LOG="$ROOT_DIR/server-console.log"

say() {
  printf '[Advance B2B GMS] %s\n' "$*"
}

fail() {
  printf '[Advance B2B GMS] ERROR: %s\n' "$*" >&2
  exit 1
}

require_file() {
  [ -f "$1" ] || fail "File not found: $1"
}

ensure_config() {
  if [ ! -f "$CONFIG_FILE" ]; then
    require_file "$ROOT_DIR/config.yaml"
    cp "$ROOT_DIR/config.yaml" "$CONFIG_FILE"
    say "Created config.local.yaml. Edit it with './server.sh config'."
  fi
  require_file "$CONFIG_FILE"
}

read_client_name() {
  if [ -n "$CLIENT_NAME" ]; then
    return
  fi
  CLIENT_NAME="$(python3 - "$CONFIG_FILE" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r"^\s*client_name:\s*([^#\n]+)", text, re.MULTILINE)
print(match.group(1).strip().strip("'\"") if match else "campaign")
PY
)"
}

tmux_session_running() {
  command -v tmux >/dev/null 2>&1 || return 1
  tmux has-session -t "$SESSION_NAME" 2>/dev/null
}

pid_is_running() {
  [ -s "$PID_FILE" ] || return 1
  local pid
  pid="$(tr -d '[:space:]' < "$PID_FILE")"
  case "$pid" in
    ''|*[!0-9]*) return 1 ;;
  esac
  kill -0 "$pid" 2>/dev/null || return 1
  if ps -p "$pid" -o args= 2>/dev/null | grep -q "scraper.main"; then
    return 0
  fi
  # The PID recorded for a tmux run is the pane shell. The Python child is
  # still authoritative while the tmux session exists.
  tmux_session_running
}

clear_stale_pid() {
  if [ -f "$PID_FILE" ] && ! pid_is_running; then
    rm -f "$PID_FILE"
  fi
}

check_not_running() {
  clear_stale_pid
  if pid_is_running || tmux_session_running; then
    fail "A scraper is already running. Use './server.sh status' or './server.sh stop'."
  fi
}

cmd_setup() {
  require_file "$ROOT_DIR/setup.sh"
  say "Installing Python, Playwright, and project dependencies."
  chmod +x "$ROOT_DIR/setup.sh" "$ROOT_DIR/run.sh" "$ROOT_DIR/server.sh" "$ROOT_DIR/vnc-screen.sh" 2>/dev/null || true
  "$ROOT_DIR/setup.sh"
  if [ ! -f "$ROOT_DIR/.env" ] && [ -f "$ROOT_DIR/.env.example" ]; then
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    say "Created .env from .env.example. Add optional API keys if needed."
  fi
  ensure_config
  say "Setup complete. Next: ./server.sh config, then ./server.sh run --demo"
}

cmd_update() {
  require_file "$ROOT_DIR/run.sh"
  check_not_running

  if ! git diff --quiet || ! git diff --cached --quiet; then
    fail "Local code changes exist. Run 'git status' and review them before updating."
  fi

  say "Downloading the latest code from GitHub main."
  git fetch origin main
  git checkout main
  git pull --ff-only origin main

  chmod +x "$ROOT_DIR/setup.sh" "$ROOT_DIR/run.sh" "$ROOT_DIR/server.sh" "$ROOT_DIR/vnc-screen.sh" 2>/dev/null || true
  ensure_config
  if [ ! -x "$ROOT_DIR/.venv/bin/python" ]; then
    fail "Python environment is missing. Run './server.sh setup' first."
  fi

  say "Refreshing Python dependencies."
  "$ROOT_DIR/.venv/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"
  say "Update complete. Next: ./server.sh run --demo, then ./server.sh run"
}

cmd_run() {
  local use_demo=false
  if [ "${1:-}" = "--demo" ]; then
    use_demo=true
  fi

  check_not_running
  ensure_config

  if [ "$use_demo" = true ]; then
    say "Running the offline demo. Google Maps is not contacted."
    bash "$ROOT_DIR/run.sh" --demo --config "$CONFIG_FILE"
    return
  fi

  read_client_name
  mkdir -p "$OUTPUT_DIR"

  if command -v tmux >/dev/null 2>&1; then
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      fail "tmux session '$SESSION_NAME' already exists. Use './server.sh status'."
    fi
    say "Starting the scraper in tmux session '$SESSION_NAME'."
    : > "$CONSOLE_LOG"
    tmux new-session -d -s "$SESSION_NAME" \
      "cd '$ROOT_DIR' && bash '$ROOT_DIR/run.sh' --config '$CONFIG_FILE' >> '$CONSOLE_LOG' 2>&1; status=\$?; rm -f '$PID_FILE'; exit \$status"
    tmux list-panes -t "$SESSION_NAME" -F '#{pane_pid}' | head -n 1 > "$PID_FILE"
    say "Started. Use './server.sh status' and './server.sh logs'."
  else
    say "tmux is not installed; starting in the background with nohup."
    : > "$CONSOLE_LOG"
    nohup bash "$ROOT_DIR/run.sh" --config "$CONFIG_FILE" >> "$CONSOLE_LOG" 2>&1 < /dev/null &
    echo "$!" > "$PID_FILE"
    say "Started. Use './server.sh status' and './server.sh logs'."
  fi
}

cmd_status() {
  clear_stale_pid
  if pid_is_running; then
    local pid
    pid="$(tr -d '[:space:]' < "$PID_FILE")"
    say "Scraper is RUNNING (PID $pid)."
    if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      say "Live terminal: tmux attach -t $SESSION_NAME"
    fi
    say "Log: $CONSOLE_LOG"
  else
    say "Scraper is STOPPED."
    if [ -f "$CONSOLE_LOG" ]; then
      say "Last log: $CONSOLE_LOG"
    fi
  fi
}

cmd_logs() {
  touch "$CONSOLE_LOG"
  if command -v less >/dev/null 2>&1; then
    less +F "$CONSOLE_LOG"
  else
    tail -f "$CONSOLE_LOG"
  fi
}

cmd_stop() {
  clear_stale_pid
  if tmux_session_running; then
    say "Requesting a graceful stop for tmux session '$SESSION_NAME'."
    tmux send-keys -t "$SESSION_NAME" C-c
    for _ in $(seq 1 30); do
      if ! tmux_session_running; then
        rm -f "$PID_FILE"
        say "Scraper stopped."
        return 0
      fi
      sleep 1
    done
    fail "The tmux session did not stop after 30 seconds. Review './server.sh logs' before forcing it."
  fi

  if ! pid_is_running; then
    say "Scraper is already stopped."
    return 0
  fi

  local pid
  pid="$(tr -d '[:space:]' < "$PID_FILE")"
  say "Requesting a graceful stop for PID $pid."
  kill "$pid"
  for _ in $(seq 1 30); do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      say "Scraper stopped."
      return 0
    fi
    sleep 1
  done
  fail "The process did not stop after 30 seconds. Review './server.sh logs' before forcing it."
}

cmd_config() {
  ensure_config
  "${EDITOR:-nano}" "$CONFIG_FILE"
}

cmd_help() {
  cat <<'HELP'
Advance B2B GMS server commands

First time:
  bash server.sh setup   Install the project and Playwright

Everyday commands:
  ./server.sh run        Start the live scraper in tmux/nohup
  ./server.sh demo       Run the offline demo; Google Maps is not contacted
  ./server.sh status     Show whether the scraper is running
  ./server.sh logs       Follow the live console log (Ctrl+C leaves scraper running)
  ./server.sh stop       Ask the running scraper to stop cleanly
  ./server.sh config     Open config.local.yaml in nano (or $EDITOR)
  ./server.sh update     Pull the latest GitHub main and refresh dependencies

The scraper reads config.local.yaml and writes output/<client_name>/.
Set ABGMS_CONFIG=/path/to/config.yaml to use another configuration file.
HELP
}

command="${1:-help}"
case "$command" in
  setup)  cmd_setup ;;
  update) cmd_update ;;
  run)    cmd_run "${2:-}" ;;
  demo)   cmd_run --demo ;;
  status) cmd_status ;;
  logs)   cmd_logs ;;
  stop)   cmd_stop ;;
  config) cmd_config ;;
  help|-h|--help) cmd_help ;;
  *) fail "Unknown command '$command'. Run './server.sh help'." ;;
esac
