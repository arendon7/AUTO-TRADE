#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-help}"

verify_outputs() {
  test -f graphify-out/graph.json || { echo "ERROR: graphify-out/graph.json missing" >&2; return 1; }
  test -f graphify-out/GRAPH_REPORT.md || { echo "ERROR: graphify-out/GRAPH_REPORT.md missing" >&2; return 1; }
  test -f graphify-out/graph.html || { echo "ERROR: graphify-out/graph.html missing" >&2; return 1; }
}

print_assistant_command() {
  local args=""
  case "$1" in
    update) args=" --update" ;;
    deep) args=" --mode deep" ;;
    full) args="" ;;
    *) echo "ERROR: unsupported semantic mode: $1" >&2; return 2 ;;
  esac

  cat >&2 <<EOF
Graphify's current build/update semantic pass runs inside a supported coding assistant.
This shell helper will not pretend to run it.

Use one of:
  Codex:  \$graphify .${args}
  Other supported assistants: /graphify .${args}

After graphify-out/ is written, run:
  bash scripts/refresh_graphify.sh stamp
EOF
}

case "$MODE" in
  stamp)
    verify_outputs
    git rev-parse HEAD > graphify-out/SOURCE_SHA
    printf 'Stamped Graphify against %s\n' "$(cat graphify-out/SOURCE_SHA)"
    ;;
  verify)
    verify_outputs
    if [[ ! -f graphify-out/SOURCE_SHA ]]; then
      echo "ERROR: graphify-out/SOURCE_SHA missing; run stamp after building the graph." >&2
      exit 1
    fi
    CURRENT="$(git rev-parse HEAD)"
    STAMPED="$(tr -d '\r\n' < graphify-out/SOURCE_SHA)"
    if [[ "$CURRENT" != "$STAMPED" ]]; then
      echo "STALE: graph was built for $STAMPED but working HEAD is $CURRENT" >&2
      exit 2
    fi
    echo "Graphify artifacts present and stamped for current HEAD: $CURRENT"
    ;;
  watch)
    if ! command -v graphify >/dev/null 2>&1; then
      echo "ERROR: graphify not found. Run: bash scripts/setup_graphify.sh" >&2
      exit 1
    fi
    echo "Starting Graphify AST watch. This does not replace the assistant semantic/deep pass."
    exec graphify watch .
    ;;
  update|deep|full)
    print_assistant_command "$MODE"
    exit 3
    ;;
  help|*)
    cat <<'EOF'
Usage: bash scripts/refresh_graphify.sh <stamp|verify|watch|update|deep|full>

  stamp   verify graph files exist and store current git HEAD in graphify-out/SOURCE_SHA
  verify  fail if graph files are missing or stamped for another HEAD
  watch   run Graphify's local AST watcher
  update  print the correct assistant command for an incremental semantic pass
  deep    print the correct assistant command for a deep semantic rebuild
  full    print the correct assistant command for a normal full build
EOF
    ;;
esac
