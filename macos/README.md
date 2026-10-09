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

Choose optional actions in the installation menu or run `./scripts/configure`
later. Explicit cleanup is disabled until selected and always requires Y/N.
Homebrew upgrades remain automatic. Optional Node.js and Bun upgrades run
automatically after selection.

## Installed Locations

| Item | Path |
| --- | --- |
| Configuration and state | `~/Library/Application Support/macos-cleanup-service/` |
| Runtime code and Python link | `~/.local/libexec/macos-cleanup-service/` |
| LaunchAgent | `~/Library/LaunchAgents/com.user.macos-cleanup-service.plist` |
| Public defaults | `config/macos.conf` in the checkout |

The LaunchAgent runs upgrades only. The shell hook requests cleanup in the
terminal first, then starts background upgrades. No journal operations run on
macOS. Homebrew upgrades at most weekly; explicit Homebrew cleanup requires
selection and approval.

## Status and Cleanup

```bash
~/.local/libexec/macos-cleanup-service/python3 ~/.local/libexec/macos-cleanup-service/status.py --json
~/.local/libexec/macos-cleanup-service/python3 ~/.local/libexec/macos-cleanup-service/status.py --cleanup-json
~/.local/libexec/macos-cleanup-service/python3 ~/.local/libexec/macos-cleanup-service/maintenance.py --cleanup-only --force
```

To request upgrades through launchd:

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
