# Use persistent encrypted session storage and the native Proton Pass agent.
export PROTON_PASS_KEY_PROVIDER=keyring
export PROTON_PASS_LINUX_KEYRING=dbus
if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
    export SSH_AUTH_SOCK="$XDG_RUNTIME_DIR/proton-pass-ssh-agent/agent.sock"
fi
