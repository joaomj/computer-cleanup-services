# Operations

The examples use Linux paths. On macOS, replace `linux-cleanup-service` with
`macos-cleanup-service`, and use the macOS configuration directory.

## Daily Schedule

The daily pass runs at 03:00 through `linux-cleanup.timer` on Linux or the
LaunchAgent calendar interval on macOS. The first interactive shell of the day
also requests the pass. The pass runs at most once per local calendar day,
including failed or interrupted attempts. The Linux timer is persistent, so a
missed 03:00 run starts after the next boot.

For background runs while you are logged out on Linux, enable lingering:

```bash
sudo loginctl enable-linger "$USER"
```

## Review High-Risk Items

When high-risk items wait for a decision and the system is short of resources,
the next interactive shell asks about each one. The prompt appears when the
disk is at least `HIGH_RISK_REVIEW_DISK_PERCENT` full, available memory is below
`HIGH_RISK_REVIEW_MEMORY_PERCENT`, or swap is at least 90% used. Otherwise the
items stay pending and the shell prints nothing.

Enter `y` to delete, `n` or Enter to keep, or `q` to stop and decide later.
The shell asks at most once per day. If the daily pass is still running, the
review waits for a later shell. `status.py --review-json` reports a deferred
review and its reason.

Every answer is recorded in `decisions.json`. A kept item returns only when it
grows by more than 25%. A deleted item that appears again is offered again.
To review now, even when the system has free space:

```bash
~/.local/libexec/linux-cleanup-service/linux-cleanup-service ~/.local/libexec/linux-cleanup-service/maintenance.py --review --force --ignore-pressure
```

To forget all recorded decisions, delete `decisions.json` in the state
directory. The next daily pass offers every current item again.

## Multiple Accounts on One Host

Install the service once per account, as that account. Each account keeps its
own configuration, pending review, decisions, and daily timer.

Only one account needs the scoped sudo rule. Accounts without it set
`SUDO_TASKS_ENABLED=false` and run user-level actions only. Their system
updates, APT cache cleanup, and system journal tasks stay disabled.

Enable lingering so an account's timer runs without a login session:

```bash
sudo loginctl enable-linger <account>
```

The installer starts each account's timer. A user manager exists only while the
account is logged in or lingering is enabled. When no user manager answers, the
installer links the timer into `timers.target.wants` instead. The timer starts
with the manager, so enable lingering first on a host with no active sessions.

## Select Actions

From a checkout:

```bash
./scripts/configure
```

Or from an installed copy:

```bash
~/.local/libexec/linux-cleanup-service/linux-cleanup-service ~/.local/libexec/linux-cleanup-service/configure.py
```

Review paths and thresholds before enabling application cleanup.

## View Results

```bash
~/.local/libexec/linux-cleanup-service/linux-cleanup-service ~/.local/libexec/linux-cleanup-service/status.py --json
~/.local/libexec/linux-cleanup-service/linux-cleanup-service ~/.local/libexec/linux-cleanup-service/status.py --security-json
~/.local/libexec/linux-cleanup-service/linux-cleanup-service ~/.local/libexec/linux-cleanup-service/status.py --review-json
```

The first command shows the daily pass. The second shows the last security
scan. The third shows the last review. Shell status stays silent unless the
last daily pass failed, found a high or critical security issue, or needs a
reboot.

## Security Findings

Run only the security scan, without upgrades or cleanup:

```bash
~/.local/libexec/linux-cleanup-service/linux-cleanup-service ~/.local/libexec/linux-cleanup-service/security.py
```

The command prints a summary and writes `security.json`. Use
`status.py --security-json` to read the full report. On Linux, install
`openscap-scanner` first; without it, the OS source reports `unavailable` and
project checks still run. On macOS, `brew vulns` requires Homebrew 7.0 or later.

The report groups findings by source and includes CVE or advisory IDs, severity,
CVSS scores when available, affected packages and versions, fixed versions when
available, and the last check time. `SECURITY_MIN_SEVERITY` controls which
findings are kept.

A required reboot appears in `security.json` under `reboot`. The pass does not
reboot the machine. After you install a kernel update, reboot when convenient.
The next scan reports the same requirement until the running kernel matches the
newest installed kernel and the reboot marker is gone.

Linux service logs:

```bash
journalctl --user -u linux-cleanup.service
```

macOS logs:

```bash
tail -n 50 "$HOME/Library/Application Support/macos-cleanup-service/launchd-stderr.log"
```

## Preview the Daily Pass

```bash
~/.local/libexec/linux-cleanup-service/linux-cleanup-service ~/.local/libexec/linux-cleanup-service/maintenance.py --dry-run
```

The preview skips upgrades and deletions, including journal vacuuming.
It lists the high-risk candidates and reports low-risk estimates.
It can create the state directory and lock file.

## Request the Daily Pass

From a checkout, request the daily pass in the background and print the progress-log command:

```bash
./scripts/upgrade
```

The daily gate and weekly Homebrew interval still apply. Commands stream output and periodic
progress messages to stderr. The default timeout is five minutes per command.
On timeout, the command process group is terminated. Completed package changes
are not rolled back. Set `COMMAND_TIMEOUT_SECONDS` to change the limit.

Homebrew plans upgrades first. If the plan reports source builds, the batch is
deferred. Otherwise, it requests bottles with `--force-bottle` without retrying
failures as source builds. This does not guarantee that every dependency or
third-party installer avoids source builds. Nonstandard prefixes can cause
more deferrals or bottle installation failures.

To request the daily pass directly through the installed service:

Linux:

```bash
systemctl --user start linux-cleanup.service
```

macOS:

```bash
launchctl start com.user.macos-cleanup-service
```

These commands respect the daily gate. For an explicit same-day retry:

```bash
~/.local/libexec/linux-cleanup-service/linux-cleanup-service ~/.local/libexec/linux-cleanup-service/maintenance.py --force
```

## Linux Privileged Commands

The installer creates `/etc/sudoers.d/90-linux-cleanup-service`.
Inspect its generated rules:

```bash
~/.local/libexec/linux-cleanup-service/linux-cleanup-service ~/.local/libexec/linux-cleanup-service/config.py sudoers-config
```

The rules permit APT metadata refresh, upgrades and cache cleanup, Snap refresh,
and journal rotation and vacuuming. The daily pass runs them without a prompt.
The installer checks `sudo -n -l` and prints a warning if the scoped rule is not
accepted. Fix the rule or set `SUDO_TASKS_ENABLED=false` before relying on system
updates.

To install for an account without sudo, run `SKIP_SUDOERS=1 ./scripts/install`
as that account and set `SUDO_TASKS_ENABLED=false`. Root-owned tasks are then
disabled for the account instead of reported as failures.

After changing `JOURNAL_SYSTEM_MAX_USE`, rerun the installer to regenerate the
allowed vacuum command. The installer does not vacuum journals or set an
automatic retention policy.

## Configuration and State

Linux stores settings and records under `~/.config/linux-cleanup-service/`,
or `XDG_CONFIG_HOME/linux-cleanup-service` when set.
macOS uses `~/Library/Application Support/macos-cleanup-service/`.
Both locations contain `environment`, `status.json`, `security.json`,
`review-status.json`, `pending-review.json`, `decisions.json`, and the
maintenance lock. Security caches use `osv-cache.json` and the `oval/`
directory. Homebrew upgrade timestamps use `brew_upgrade.json`.
Do not add these files to Git.

## Optional SSH Agent

The Proton Pass agent has its own installer and lifecycle. Maintenance does not
start it or manage its credentials. See [Proton Pass setup](proton-pass.md) for
installation, single-key selection, commit signing, recovery, and removal.
