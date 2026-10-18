#!/usr/bin/env bash

# Start the daily pass in the background; it runs at most once a day.
if command -v systemctl >/dev/null 2>&1 && systemctl --user is-system-running >/dev/null 2>&1; then
    systemctl --user start --no-block linux-cleanup.service >/dev/null 2>&1 || true
fi

# Ask about high-risk items only when some are waiting for a decision.
if [ -t 0 ] && [ -t 1 ] && [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/linux-cleanup-service/pending-review.json" ]; then
    "$HOME/.local/libexec/linux-cleanup-service/python3" "$HOME/.local/libexec/linux-cleanup-service/maintenance.py" --review --quiet
fi

if [ -x "$HOME/.local/libexec/linux-cleanup-service/status.py" ]; then
    "$HOME/.local/libexec/linux-cleanup-service/python3" "$HOME/.local/libexec/linux-cleanup-service/status.py" 2>/dev/null || true
fi
