#!/bin/sh

# Cleanup runs in the terminal so deletions can require explicit approval.
if [ -t 0 ] && [ -t 1 ] && [ -f "$HOME/.local/libexec/macos-cleanup-service/maintenance.py" ]; then
    "$HOME/.local/libexec/macos-cleanup-service/python3" "$HOME/.local/libexec/macos-cleanup-service/maintenance.py" --cleanup-only
fi

if command -v launchctl >/dev/null 2>&1; then
    launchctl start com.user.macos-cleanup-service >/dev/null 2>&1 || true
fi

if [ -x "$HOME/.local/libexec/macos-cleanup-service/status.py" ]; then
    "$HOME/.local/libexec/macos-cleanup-service/python3" "$HOME/.local/libexec/macos-cleanup-service/status.py" 2>/dev/null || true
fi
