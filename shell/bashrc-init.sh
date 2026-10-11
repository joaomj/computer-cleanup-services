#!/usr/bin/env bash

# Start automatic upgrades in the background.
if command -v systemctl >/dev/null 2>&1 && systemctl --user is-system-running >/dev/null 2>&1; then
    systemctl --user start --no-block linux-cleanup.service >/dev/null 2>&1 || true
fi

if [ -x "$HOME/.local/libexec/linux-cleanup-service/status.py" ]; then
    "$HOME/.local/libexec/linux-cleanup-service/python3" "$HOME/.local/libexec/linux-cleanup-service/status.py" 2>/dev/null || true
fi
