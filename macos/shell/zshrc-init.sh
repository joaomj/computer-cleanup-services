#!/bin/sh

SERVICE_DIR="$HOME/.local/libexec/macos-cleanup-service"
PYTHON="$SERVICE_DIR/python3"

# Start the daily pass in the background; it runs at most once a day.
if command -v launchctl >/dev/null 2>&1; then
    launchctl start com.user.macos-cleanup-service >/dev/null 2>&1 || true
fi

# Ask about high-risk items only when some are waiting for a decision.
if [ -x "$PYTHON" ] && [ -t 0 ] && [ -t 1 ] && [ -f "${XDG_CONFIG_HOME:-$HOME/Library/Application Support}/macos-cleanup-service/pending-review.json" ]; then
    "$PYTHON" "$SERVICE_DIR/maintenance.py" --review --quiet
fi

if [ -x "$PYTHON" ] && [ -x "$SERVICE_DIR/status.py" ]; then
    "$PYTHON" "$SERVICE_DIR/status.py" 2>/dev/null || true
fi
