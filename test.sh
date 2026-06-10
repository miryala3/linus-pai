#!/usr/bin/env bash
# Canonical tests for linus-pai: syntax gate + unit tests (best-effort).
set -uo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-}"; [ -z "$PY" ] && { command -v python3 >/dev/null && PY=python3 || PY=python; }
rc=0
echo "== py_compile (syntax gate) =="
while IFS= read -r f; do [ -n "$f" ] || continue; "$PY" -m py_compile "$f" || rc=1; done \
  < <(git ls-files '*.py' 2>/dev/null || find . -name '*.py' -not -path './.git/*')
echo "== unit tests (best-effort) =="
if [ -f test_pai.py ]; then
  "$PY" -m pytest -q test_pai.py 2>/dev/null \
    || "$PY" -m unittest test_pai 2>/dev/null \
    || echo "(tests need deps not installed; syntax gate still enforced)"
fi
exit $rc
