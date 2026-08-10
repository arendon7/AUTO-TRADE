#!/usr/bin/env bash
set -euo pipefail

PLATFORM="${1:-}"

if command -v uv >/dev/null 2>&1; then
  uv tool install graphifyy || uv tool upgrade graphifyy
elif command -v pipx >/dev/null 2>&1; then
  pipx install graphifyy || pipx upgrade graphifyy
else
  echo "ERROR: install uv or pipx first." >&2
  exit 1
fi

if [[ -n "$PLATFORM" ]]; then
  graphify install --platform "$PLATFORM"
else
  graphify install
fi

cat <<'EOF'
Graphify CLI/skill registration complete.

IMPORTANT: current Graphify builds the initial/semantic graph INSIDE a supported coding assistant, not with `graphify .` in a shell.

Examples after opening this repo in a supported assistant:
  Codex:  $graphify . --mode deep
  Others: /graphify . --mode deep

Then commit graphify-out/ and stamp its source SHA with:
  bash scripts/refresh_graphify.sh stamp
EOF
