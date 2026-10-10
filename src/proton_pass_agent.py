#!/usr/bin/env python3
"""Start the native agent only after the desktop keyring is unlocked."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main() -> int:
    for name, expected in (
        ("PROTON_PASS_KEY_PROVIDER", "keyring"),
        ("PROTON_PASS_LINUX_KEYRING", "dbus"),
    ):
        if os.environ.get(name) != expected:
            raise RuntimeError(f"Set {name}={expected} before starting the agent")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime or not Path(runtime).is_dir():
        raise RuntimeError("XDG_RUNTIME_DIR must point to the desktop user's runtime directory")
    executable = Path(__file__).resolve().parent / "pass-cli"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError("pass-cli is unavailable; rerun scripts/install-proton-pass")
    if not shutil.which("busctl"):
        raise RuntimeError("busctl is required; install the systemd tools")

    command = [
        "busctl", "--user", "--timeout=5", "get-property",
        "org.freedesktop.secrets", "/org/freedesktop/secrets/aliases/default",
        "org.freedesktop.Secret.Collection", "Locked",
    ]
    deadline = time.monotonic() + 120
    while True:
        result = subprocess.run(command, text=True, capture_output=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip() == "b false":
            break
        if result.returncode == 0 and result.stdout.strip() != "b true":
            raise RuntimeError(f"Unexpected Secret Service lock state: {result.stdout!r}")
        detail = result.stderr.strip() if result.returncode else "Desktop keyring is locked"
        if time.monotonic() >= deadline:
            print(
                f"{detail}. Unlock the default desktop keyring, then run "
                "systemctl --user restart proton-pass-ssh-agent.service. "
                "Proton Pass was not started.", file=sys.stderr,
            )
            return 78
        print(f"Waiting for desktop keyring: {detail}", file=sys.stderr)
        time.sleep(2)

    socket = str(Path(runtime) / "proton-pass-ssh-agent" / "agent.sock")
    os.execv(str(executable), [str(executable), "ssh-agent", "start", "--socket-path", socket])


if __name__ == "__main__":
    sys.exit(main())
