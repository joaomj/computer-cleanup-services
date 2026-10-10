# Operations

The examples use Linux paths. On macOS, replace `linux-cleanup-service` with
`macos-cleanup-service`, and use the macOS configuration directory.

## Select Actions

From a checkout:

```bash
./scripts/configure
```

Or from an installed copy:

```bash
~/.local/libexec/linux-cleanup-service/python3 ~/.local/libexec/linux-cleanup-service/configure.py
```

Review paths and thresholds before enabling application cleanup.

## View Results

```bash
~/.local/libexec/linux-cleanup-service/python3 ~/.local/libexec/linux-cleanup-service/status.py --json
~/.local/libexec/linux-cleanup-service/python3 ~/.local/libexec/linux-cleanup-service/status.py --cleanup-json
```

The first command shows upgrades. The second shows the cleanup decision and
result. Shell status stays silent unless the last upgrade pass failed.
Cleanup command failures are printed in the requesting terminal.

Linux service logs:

```bash
journalctl --user -u linux-cleanup.service
```

macOS logs:

```bash
tail -n 50 "$HOME/Library/Application Support/macos-cleanup-service/launchd-stderr.log"
```

## Preview Cleanup

```bash
~/.local/libexec/linux-cleanup-service/python3 ~/.local/libexec/linux-cleanup-service/maintenance.py --cleanup-only --dry-run --force
```

The preview skips deletion commands and upgrades, including journal vacuuming.
It can create the state directory and lock file. It reports estimates and
selected action results, not a fixed file inventory.

## Request Approved Cleanup

Run from an interactive terminal:

```bash
~/.local/libexec/linux-cleanup-service/python3 ~/.local/libexec/linux-cleanup-service/maintenance.py --cleanup-only --force
```

Inspect the scopes and preview. Enter Y to approve or N to decline.
Enter, interrupted input, or no terminal means no deletion.
The approval covers one pass. A later pass must ask again.

If another maintenance pass holds the lock, retry after it finishes.
Changing the selected actions does not reset today's decision; use `--force`
when you want to preview a new selection immediately.

## Request Upgrades

From a checkout, request background upgrades and print the progress-log command:

```bash
./scripts/upgrade
```

The daily and weekly schedules still apply. Commands stream output and periodic
progress messages to stderr. The default timeout is five minutes per command.
On timeout, the command process group is terminated. Completed package changes
are not rolled back. Set `COMMAND_TIMEOUT_SECONDS` to change the limit.

Homebrew plans upgrades first. If the plan reports source builds, the batch is
deferred. Otherwise, it requests bottles with `--force-bottle` without retrying
failures as source builds. This does not guarantee that every dependency or
third-party installer avoids source builds. Nonstandard prefixes can cause
more deferrals or bottle installation failures.

To request upgrades directly through the installed service:

Linux:

```bash
systemctl --user start linux-cleanup.service
```

macOS:

```bash
launchctl start com.user.macos-cleanup-service
```

These commands request upgrades only and respect the daily gate.
For an explicit same-day retry:

```bash
~/.local/libexec/linux-cleanup-service/python3 ~/.local/libexec/linux-cleanup-service/maintenance.py --updates-only --force
```

## Linux Privileged Commands

The installer creates `/etc/sudoers.d/90-linux-cleanup-service`.
Inspect its generated rules:

```bash
~/.local/libexec/linux-cleanup-service/python3 ~/.local/libexec/linux-cleanup-service/config.py sudoers-config
```

The rules permit APT metadata refresh, upgrades and cache cleanup, Snap refresh,
and journal rotation and vacuuming. Cleanup commands still require terminal
approval through the maintenance entry point. A sudoers rule itself does not
enforce this prompt; it grants the account those exact commands.

After changing `JOURNAL_SYSTEM_MAX_USE`, rerun the installer to regenerate the
allowed vacuum command. The installer does not vacuum journals or set an
automatic retention policy.

## Configuration and State

Linux stores settings and records under `~/.config/linux-cleanup-service/`,
or `XDG_CONFIG_HOME/linux-cleanup-service` when set.
macOS uses `~/Library/Application Support/macos-cleanup-service/`.
Both locations contain `environment`, `status.json`, `cleanup-status.json`,
and the maintenance lock. Homebrew upgrade timestamps use `brew_upgrade.json`.
Do not add these files to Git.

## Optional SSH Agent

The Proton Pass agent has its own installer and lifecycle. Maintenance does not
start it or manage its credentials. See [Proton Pass setup](proton-pass.md) for
installation, single-key selection, commit signing, recovery, and removal.
