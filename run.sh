#!/usr/bin/env bash
# Convenience wrapper: creates/uses the project's .venv and runs trading_strategy.py
# with whatever arguments you pass, so you don't need to activate the venv yourself.
#
# Usage examples:
#   ./run.sh --list-universe
#   ./run.sh --test-history --quarters 20 --capital 25000
#   ./run.sh --run --holdings my_holdings.json --output today.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "No virtualenv found at $VENV_DIR — creating one..." >&2
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
fi

exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/trading_strategy.py" "$@"
