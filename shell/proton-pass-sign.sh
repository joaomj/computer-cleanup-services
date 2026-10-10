#!/usr/bin/env bash
set -euo pipefail
if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
    printf 'XDG_RUNTIME_DIR is unset. Run Git from your Linux desktop user session.\n' >&2
    exit 1
fi
export SSH_AUTH_SOCK="$XDG_RUNTIME_DIR/proton-pass-ssh-agent/agent.sock"
if [[ ! -S "$SSH_AUTH_SOCK" ]]; then
    printf 'Proton SSH agent socket is unavailable: %s. Start proton-pass-ssh-agent.service.\n' "$SSH_AUTH_SOCK" >&2
    exit 1
fi
exec ssh-keygen "$@"
