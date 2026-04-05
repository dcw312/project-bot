#!/usr/bin/env bash
# Usage: ./start.sh django | ./start.sh bot
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "Error: virtualenv not found. Run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

case "${1:-}" in
    django)
        echo "Starting Django admin server at http://127.0.0.1:8000/"
        "$PYTHON" "$SCRIPT_DIR/manage.py" runserver
        ;;
    bot)
        echo "Starting Discord bot..."
        "$PYTHON" "$SCRIPT_DIR/manage.py" run_bot
        ;;
    *)
        echo "Usage: $0 <django|bot>"
        exit 1
        ;;
esac
