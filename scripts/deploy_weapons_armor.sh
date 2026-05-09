#!/usr/bin/env bash
# deploy_weapons_armor.sh
# Uploads the weapons page, updated survival page, nav, run.py, and sitemap
# to the live IONOS webspace. Run from the repo root.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="$PWD/venv/bin/python"
HELPER="$PWD/scripts/deploy_weapons_armor.py"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Error: expected Python venv at $PYTHON_BIN"
  exit 1
fi

if [[ ! -f "$HELPER" ]]; then
  echo "Error: deploy helper missing at $HELPER"
  exit 1
fi

if [[ $# -eq 0 ]]; then
  set -- deploy
fi

exec "$PYTHON_BIN" "$HELPER" "$@"
