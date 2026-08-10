#!/usr/bin/env bash
set -euo pipefail

if command -v uv >/dev/null 2>&1; then
  uv tool install graphifyy || uv tool upgrade graphifyy
else
  echo "ERROR: uv is required. Install uv first, then rerun." >&2
  exit 1
fi

# Install the generic Agent Skills integration inside this repository.
graphify install --platform agents --project

echo "Graphify project skill installed. Build the initial graph with: graphify ."
