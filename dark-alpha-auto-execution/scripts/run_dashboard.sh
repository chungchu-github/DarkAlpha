#!/usr/bin/env bash
# Dark Alpha localhost-only monitoring dashboard.
# Designed to run in parallel to the signal receiver (port 8765) and the
# supervisor — restarting it never disturbs the signal hot path.
#
# Usage:
#   ./scripts/run_dashboard.sh            # foreground
#   nohup ./scripts/run_dashboard.sh &    # detached
set -euo pipefail
cd "$(dirname "$0")/.."
exec poetry run uvicorn dashboard.app:app --host 127.0.0.1 --port 8766 "$@"
