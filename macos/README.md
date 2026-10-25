# macOS Service

The macOS service in `computer-cleanup-services` uses launchd.
Its installed name is `macos-cleanup-service` and its job label is
`com.user.macos-cleanup-service`.

## Install

From the repository root:

```bash
./scripts/install
```

The installer detects macOS, copies runtime files, records the selected Python
interpreter, registers the LaunchAgent, and adds a hook to the shell selected
by `SHELL`. The default macOS shell is zsh.

Every cleanup action is on in a new installation. Change the selection in the
installation menu or with `./scripts/configure`. Homebrew upgrades and low-risk
cleanup are automatic. High-risk items wait for a recorded decision.

## Installed Locations

| Item | Path |
| --- | --- |
| Configuration and state | `~/Library/Application Support/macos-cleanup-service/` |
| Runtime code and Python launcher | `~/.local/libexec/macos-cleanup-service/` |
| LaunchAgent | `~/Library/LaunchAgents/com.user.macos-cleanup-service.plist` |
| Public defaults | `config/macos.conf` in the checkout |

The LaunchAgent runs the daily pass at 03:00. The shell hook also requests it
on the first shell of the day, then asks about high-risk items when some wait
and the system is short of disk space or memory.
No journal operations run on macOS. Homebrew upgrades at most weekly.

The daily pass checks Homebrew formulae through `brew vulns` and project
lockfiles through OSV. It needs Homebrew 7.0 or later. macOS does not use the
Ubuntu OVAL scan. Findings at or above `SECURITY_MIN_SEVERITY` are written to
`security.json`; the shell line reports high or critical findings.

## Status and Cleanup

```bash
~/.local/libexec/macos-cleanup-service/macos-cleanup-service ~/.local/libexec/macos-cleanup-service/status.py --json
~/.local/libexec/macos-cleanup-service/macos-cleanup-service ~/.local/libexec/macos-cleanup-service/status.py --security-json
~/.local/libexec/macos-cleanup-service/macos-cleanup-service ~/.local/libexec/macos-cleanup-service/status.py --review-json
~/.local/libexec/macos-cleanup-service/macos-cleanup-service ~/.local/libexec/macos-cleanup-service/maintenance.py --review --force --ignore-pressure
```

To request the daily pass through launchd:

```bash
launchctl start com.user.macos-cleanup-service
```

The daily gate still applies. See [operations](../docs/operations.md) for
previews, same-day retries, logs, and configuration changes.

## Uninstall

```bash
./scripts/uninstall
```

The uninstaller removes the job, runtime copy, and shell hook.
Configuration and state remain. It does not uninstall optional runtime versions.
