#!/usr/bin/env bash
# Canonical run for linus-pai. Usage: ./run.sh [args...]
set -uo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-}"; [ -z "$PY" ] && { command -v python3 >/dev/null && PY=python3 || PY=python; }
[ -f pai.py ] || { echo "run.sh: pai.py not found" >&2; exit 1; }
exec "$PY" pai.py "$@"
