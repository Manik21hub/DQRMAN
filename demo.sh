#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

source /home/lenovomanik/projects/oqs-env/bin/activate
export PYTHONPATH=/home/lenovomanik/projects/DQRMAN

# If a server is already active on 8080, keep this script alive for demo lifecycle
# commands instead of failing with "address already in use".
if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then
	trap 'exit 0' INT TERM
	while true; do
		sleep 60
	done
fi

exec python backend/server.py --nodes 10 --log-level INFO
