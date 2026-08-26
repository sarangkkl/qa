#!/usr/bin/env bash
# The single definition of "passing". Used by: manual runs, Claude Code hook, git pre-commit.
#   ./check.sh       verify only (CI-safe)
#   ./check.sh fix   auto-format and auto-fix first
set -euo pipefail
cd "$(dirname "$0")"
PY=venv/bin

if [ "${1:-}" = 'fix' ]; then
	"$PY/ruff" format nkqa tests
	"$PY/ruff" check --fix nkqa tests
else
	"$PY/ruff" format --check nkqa tests
	"$PY/ruff" check nkqa tests
fi
"$PY/pyright" --project pyproject.toml
"$PY/pytest" -q
echo '✅ all checks passed'
