#!/usr/bin/env bash
#
# Launches receiver + supervisor + (optional) telegram bot under tmux.
# Each in its own pane so you can detach/reattach and keep logs visible.
#
# Usage:
#   ./scripts/start-all.sh          # receiver + supervisor
#   ./scripts/start-all.sh --tg     # also start the telegram bot
#
# Detach: Ctrl-B then D
# Reattach:  tmux attach -t dark-alpha
# Kill all:  tmux kill-session -t dark-alpha

set -euo pipefail

SESSION="dark-alpha"
WITH_TG=""
for arg in "$@"; do
  case "$arg" in
    --tg|--telegram) WITH_TG=1 ;;
  esac
done

cd "$(dirname "$0")/.."

if ! command -v tmux >/dev/null 2>&1; then
  echo "❌ tmux not installed. brew install tmux  (or run the three commands manually)"
  exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "❌ tmux session '$SESSION' already exists. tmux attach -t $SESSION"
  exit 1
fi

echo "▶ dark-alpha doctor"
poetry run dark-alpha doctor || true

echo
echo "▶ starting tmux session '$SESSION'"

tmux new-session -d -s "$SESSION" -n receiver \
  "poetry run uvicorn signal_adapter.receiver:app --host 127.0.0.1 --port 8765 --log-level info"

tmux new-window -t "$SESSION" -n supervisor \
  "poetry run dark-alpha run"

if [[ -n "$WITH_TG" ]]; then
  tmux new-window -t "$SESSION" -n telegram \
    "poetry run dark-alpha telegram"
fi

echo "✓ started. Attach with:  tmux attach -t $SESSION"
echo "  Windows: receiver (port 8765), supervisor${WITH_TG:+, telegram}"
echo "  Switch:  Ctrl-B (release) then w / n / p / 0-2 / d (detach)"
