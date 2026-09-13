#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_APP="$PROJECT_DIR/dist/SixSentences Companion.app"
DESTINATION_APP="$HOME/Applications/SixSentences Companion.app"

"$SCRIPT_DIR/build-app.sh"
mkdir -p "$HOME/Applications"
ditto "$SOURCE_APP" "$DESTINATION_APP"
echo "Installed at $DESTINATION_APP"
