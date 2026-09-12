#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"

for script in "$SCRIPT_DIR"/*.sh; do
  bash -n "$script"
done
python3 -m unittest discover --start-directory "$SCRIPT_DIR/tests" --pattern 'test_*.py'
