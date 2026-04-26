#!/usr/bin/env bash
#
# Gate 6.7 burn-in harness.
#
# Continuously runs receiver + supervisor + user_stream + signals service for
# the requested duration, captures gate-check + readiness snapshots every
# `BURN_IN_SNAPSHOT_INTERVAL_SECONDS`, and writes a final report to
# `docs/burn-in-<DATE>.md`.
#
# Usage:
#   BURN_IN_HOURS=24 ./scripts/burn-in.sh
#   BURN_IN_HOURS=72 BURN_IN_SNAPSHOT_INTERVAL_SECONDS=3600 ./scripts/burn-in.sh
#
# Pre-conditions:
#   - main.yaml must be in shadow OR live/testnet mode (script refuses mainnet)
#   - poetry env installed; dark-alpha doctor returns clean
#   - signals service has been started separately (or pass --start-signals)
#
# Detach during run: Ctrl-B then D (tmux session: dark-alpha-burn-in)
# Stop early:        tmux send-keys -t dark-alpha-burn-in:0 C-c

set -euo pipefail

SESSION="dark-alpha-burn-in"
BURN_IN_HOURS="${BURN_IN_HOURS:-24}"
BURN_IN_SNAPSHOT_INTERVAL_SECONDS="${BURN_IN_SNAPSHOT_INTERVAL_SECONDS:-3600}"
START_SIGNALS=""
for arg in "$@"; do
  case "$arg" in
    --start-signals) START_SIGNALS=1 ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
  esac
done

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
BURN_IN_DATE="$(date -u +%Y-%m-%dT%H%M%SZ)"
BURN_IN_DIR="${REPO_ROOT}/docs/burn-in-${BURN_IN_DATE}"
mkdir -p "${BURN_IN_DIR}"

echo "▶ burn-in starting"
echo "  date    : ${BURN_IN_DATE}"
echo "  hours   : ${BURN_IN_HOURS}"
echo "  snapshot: every ${BURN_IN_SNAPSHOT_INTERVAL_SECONDS}s"
echo "  output  : ${BURN_IN_DIR}"

# Refuse mainnet — burn-in must be on shadow/testnet only.
MODE_ENV=$(poetry run python -c '
from execution.live_safety import load_live_execution_config
cfg = load_live_execution_config()
print(f"{cfg.mode}/{cfg.environment}")
')
case "${MODE_ENV}" in
  shadow/*|live/testnet)
    echo "  config  : ${MODE_ENV} (allowed)"
    ;;
  *)
    echo "❌ burn-in refuses ${MODE_ENV} — only shadow or live/testnet permitted."
    exit 2
    ;;
esac

if ! command -v tmux >/dev/null 2>&1; then
  echo "❌ tmux not installed. brew install tmux"
  exit 1
fi

if tmux has-session -t "${SESSION}" 2>/dev/null; then
  echo "❌ tmux session '${SESSION}' already exists. tmux attach -t ${SESSION}"
  exit 1
fi

# Doctor pass.
echo
echo "▶ pre-flight: dark-alpha doctor"
poetry run dark-alpha doctor || true

# Spawn services in tmux.
echo
echo "▶ launching tmux services"
tmux new-session -d -s "${SESSION}" -n receiver \
  "cd '${REPO_ROOT}' && poetry run uvicorn signal_adapter.receiver:app --host 127.0.0.1 --port 8765 2>&1 | tee '${BURN_IN_DIR}/receiver.log'"
tmux new-window  -t "${SESSION}" -n supervisor \
  "cd '${REPO_ROOT}' && poetry run dark-alpha run 2>&1 | tee '${BURN_IN_DIR}/supervisor.log'"
tmux new-window  -t "${SESSION}" -n user-stream \
  "cd '${REPO_ROOT}' && poetry run dark-alpha user-stream listen 2>&1 | tee '${BURN_IN_DIR}/user-stream.log' || echo '(user-stream not eligible in this mode)'"

if [[ -n "${START_SIGNALS}" ]]; then
  tmux new-window -t "${SESSION}" -n signals \
    "cd '${REPO_ROOT}/../dark-alpha-signals' && poetry run python -m dark_alpha_phase_one.main 2>&1 | tee '${BURN_IN_DIR}/signals.log'"
fi

DEADLINE=$(($(date +%s) + BURN_IN_HOURS * 3600))
SNAPSHOT_INDEX=0

echo
echo "▶ services running. Snapshots will be saved to ${BURN_IN_DIR}/snapshot-NNN.md"
echo "  Detach:   Ctrl-B then D"
echo "  Reattach: tmux attach -t ${SESSION}"
echo

# Snapshot loop.
while [[ $(date +%s) -lt ${DEADLINE} ]]; do
  SNAPSHOT_INDEX=$((SNAPSHOT_INDEX + 1))
  SNAPSHOT_FILE="${BURN_IN_DIR}/snapshot-$(printf '%03d' ${SNAPSHOT_INDEX}).md"
  TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  {
    echo "# Burn-in snapshot ${SNAPSHOT_INDEX} — ${TS}"
    echo
    echo "## dark-alpha status"
    echo
    echo '```'
    poetry run dark-alpha status 2>&1 || true
    echo '```'
    echo
    echo "## gate-check all"
    echo
    poetry run dark-alpha gate-check all 2>&1 || true
    echo
    echo "## reconcile-live"
    echo
    echo '```'
    poetry run dark-alpha reconcile-live 2>&1 || true
    echo '```'
  } > "${SNAPSHOT_FILE}"

  echo "  [$(date -u +%H:%M:%S)] snapshot ${SNAPSHOT_INDEX} → ${SNAPSHOT_FILE}"
  sleep "${BURN_IN_SNAPSHOT_INTERVAL_SECONDS}"
done

echo
echo "▶ burn-in window elapsed. Generating final report."

REPORT="${BURN_IN_DIR}/report.md"
{
  echo "# Burn-in Report — ${BURN_IN_DATE}"
  echo
  echo "- duration:           ${BURN_IN_HOURS}h"
  echo "- mode/environment:   ${MODE_ENV}"
  echo "- snapshots captured: ${SNAPSHOT_INDEX}"
  echo "- snapshot interval:  ${BURN_IN_SNAPSHOT_INTERVAL_SECONDS}s"
  echo
  echo "## Final dark-alpha status"
  echo
  echo '```'
  poetry run dark-alpha status 2>&1 || true
  echo '```'
  echo
  echo "## Final Gate 6 readiness"
  echo
  poetry run dark-alpha gate6 readiness --recent-stream-minutes 30 --burn-in-hours "${BURN_IN_HOURS}" 2>&1 || true
  echo
  echo "## Performance"
  echo
  poetry run dark-alpha report performance 2>&1 || true
  echo
  echo "## Daily snapshot (today)"
  echo
  poetry run dark-alpha report daily 2>&1 || true
} > "${REPORT}"

echo "▶ report written to ${REPORT}"
echo "▶ stopping tmux session"
tmux kill-session -t "${SESSION}" 2>/dev/null || true

echo
echo "✓ burn-in complete. Review ${REPORT}, then commit ${BURN_IN_DIR}/ to docs."
