#!/bin/bash
# Install repo-pilot CLI
set -e

INSTALL_DIR="${1:-$HOME/.local/bin}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/run.sh" "$INSTALL_DIR/repo-pilot"
chmod +x "$INSTALL_DIR/repo-pilot"

echo "Installed repo-pilot to $INSTALL_DIR/repo-pilot"

if ! echo "$PATH" | grep -q "$INSTALL_DIR"; then
    echo "Note: $INSTALL_DIR is not in your PATH. Add it with:"
    echo "  export PATH=\"$INSTALL_DIR:\$PATH\""
fi
