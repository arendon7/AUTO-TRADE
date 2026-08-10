#!/usr/bin/env bash
set -euo pipefail

if ! command -v graphify >/dev/null 2>&1; then
  echo "ERROR: graphify not found. Run: bash scripts/setup_graphify.sh" >&2
  exit 1
fi

MODE="${1:-update}"
case "$MODE" in
  update)
    graphify . --update
    ;;
  deep)
    graphify . --mode deep
    ;;
  full)
    graphify .
    ;;
  *)
    echo "Usage: $0 [update|deep|full]" >&2
    exit 2
    ;;
esac

test -f graphify-out/graph.json
test -f graphify-out/GRAPH_REPORT.md
test -f graphify-out/graph.html

echo "Graphify refreshed successfully ($MODE)."
