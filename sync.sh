#!/usr/bin/env bash
# sync.sh — thin wrapper around sync.py.
# Regenerates manifest.yaml's repos: section from workspaces/*.code-workspace,
# grouped and ordered by workspace so there are no duplicates or drift.
#
# Usage:
#   ./sync.sh                 # rewrite manifest
#   ./sync.sh --dry-run       # preview only

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Pick a python that actually runs. On Windows, `python3` on PATH is often
# the Microsoft Store stub that exits 49 when Store Python isn't installed,
# so `command -v` alone is not enough — probe with --version.
PY=""
for cand in python python3 py; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" --version >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done
if [[ -z "$PY" ]]; then
  echo "✗ no working python found on PATH (tried: python, python3, py)" >&2
  exit 1
fi

exec "$PY" "$SCRIPT_DIR/sync.py" "$@"
