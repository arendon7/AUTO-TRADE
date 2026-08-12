#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

MANIFEST="$ROOT/PACKAGE_MANIFEST.sha256"
if [[ ! -f "$MANIFEST" ]]; then
  echo "AVISO: PACKAGE_MANIFEST.sha256 no está presente; esto parece un checkout de código, no el bundle FULL publicado."
  exit 0
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
while IFS= read -r line; do
  file="${line#*  }"
  case "$file" in
    ./PACKAGE_MANIFEST.sha256|./MAC_FULL_INSTALL_RECEIPT.json|./.venv/*) continue ;;
  esac
  printf '%s\n' "$line" >> "$TMP"
done < "$MANIFEST"

/usr/bin/shasum -a 256 -c "$TMP"
echo "AUTO-TRADE package integrity: PASS"
