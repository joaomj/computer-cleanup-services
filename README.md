# computer-cleanup-services

Daily maintenance for Linux and macOS. Upgrades and low-risk cleanup run
automatically. High-risk cleanup asks once per item, records the answer, and
prompts only when the system is short of disk space or memory.

The maintenance code supports two platform services:

- Linux: `linux-cleanup.service` and `linux-cleanup.timer`, stored under
  `linux-cleanup-service`.
- macOS: `com.user.macos-cleanup-service`, stored under `macos-cleanup-service`.

## How It Works

The daily pass runs at 03:00 and when the first shell of the day opens.
It runs at most once per local calendar day. The pass does these steps:

1. Runs package upgrades.
2. Checks OS packages, project dependencies, and Homebrew for known
   vulnerabilities. It also records whether a reboot is required.
3. Deletes low-risk data without a prompt. Low-risk data is regenerable:
   package-manager caches, old temporary files, and similar data.
4. Finds high-risk candidates and stores the undecided ones for review.
   High-risk data can contain application state or take long to restore.

When an interactive shell opens, items wait for review, and the system is
short of resources, the shell asks about each item. The prompt appears when the
disk is at least `HIGH_RISK_REVIEW_DISK_PERCENT` full, available memory is
below `HIGH_RISK_REVIEW_MEMORY_PERCENT`, or swap is at least 90% used.
Otherwise the items wait for a later day. That way a large cleanup is offered
when it helps, not every day.

Enter `y` to delete, `n` or Enter to keep, or `q` to stop. Every answer is
recorded. A kept item is offered again only when it grows by
more than 25%. When no items wait, the shell does not prompt.

The pass checks each high-risk item again immediately before deletion. An item
that is now in use, recent, tracked by Git, or changed in another way is kept.

## Requirements

- Python 3.10 or later on `PATH`, Bash, and Git.
- Linux with a systemd user manager, or macOS with launchd.
- An interactive Bash or zsh shell for the startup hook and review prompt.
- Tools for the actions you use. Actions for missing tools are skipped.
- `openscap-scanner` on Linux for OS CVE checks. Project checks still run
  without it.
- Homebrew 7.0 or later on macOS for `brew vulns` checks.
- GnuPG tools and Apple Silicon if you enable verified macOS runtime upgrades.

Linux package upgrades support APT and Snap, not every distribution's package
manager. Linux installation uses `sudo` and `visudo` for scoped update access.
Run the installer as your regular user, not root.

## Install and Choose Actions

```bash
git clone https://github.com/joaomj/computer-cleanup-services.git
cd computer-cleanup-services
./scripts/install
```

The installer selects the operating system, copies the code, registers the
service and daily schedule, and adds a shell startup hook. Every cleanup action
is on in a new installation. Existing configuration is preserved.
The installer offers an action menu. Change the selection later with:

```bash
./scripts/configure
```

Use `./scripts/upgrade` to request the daily pass and print the progress-log command.
See [operations](docs/operations.md#request-the-daily-pass) for scheduling and timeout behavior.

## Configuration

Public templates use `KEY=VALUE`, not shell code:

- Linux: `config/default.conf`
- macOS: `config/macos.conf`

| Platform | Installed configuration and state | Installed code |
| --- | --- | --- |
| Linux | `~/.config/linux-cleanup-service/` | `~/.local/libexec/linux-cleanup-service/` |
| macOS | `~/Library/Application Support/macos-cleanup-service/` | `~/.local/libexec/macos-cleanup-service/` |

Linux uses `XDG_CONFIG_HOME` when set. Configuration is stored in the
`environment` file in the configuration directory. Process variables override
file settings. The action menu replaces the previous optional-action selection
and preserves other settings.

Read the [configuration reference](docs/configuration.md) for flags, paths,
thresholds, and update settings.

## Inspect, Review, and Check Results

From a checkout, preview the daily pass without upgrades or deletions:

```bash
python3 src/maintenance.py --dry-run --force
```

Review waiting high-risk items now, even after a review earlier today, and
even when the system has free space:

```bash
python3 src/maintenance.py --review --force --ignore-pressure
```

A review without pressure does not prompt. It records `deferred` in
`review-status.json` and keeps the items pending.

On Linux, inspect the daily pass, the security report, and the last review:

```bash
~/.local/libexec/linux-cleanup-service/linux-cleanup-service ~/.local/libexec/linux-cleanup-service/status.py --json
~/.local/libexec/linux-cleanup-service/linux-cleanup-service ~/.local/libexec/linux-cleanup-service/status.py --security-json
~/.local/libexec/linux-cleanup-service/linux-cleanup-service ~/.local/libexec/linux-cleanup-service/status.py --review-json
```

On macOS, replace `linux-cleanup-service` with `macos-cleanup-service`.
A dry run can create the private state directory and lock file.

The shell line reports high or critical security findings and a required
reboot. Other cleanup details remain in the JSON output.

## Security Monitoring

The daily pass checks three sources after package upgrades:

- Ubuntu OS packages and the kernel with Canonical OVAL and OpenSCAP. It
  reports installed packages affected by known CVEs, including CVEs without a
  published fix.
- Project lockfiles under `PROJECT_ROOT` with OSV.
- Homebrew formulae on macOS with `brew vulns`.

The pass records findings at or above `SECURITY_MIN_SEVERITY` (default `high`)
in `security.json`. It also records whether a reboot is required to activate an
installed kernel or other update. The shell line shows a warning when the last
pass found a high or critical issue, or when a reboot is required.

The scanner reports findings. It does not upgrade project dependencies or
remove affected packages.

Limits:

- OS scanning supports Ubuntu only.
- Project scanning parses npm, pnpm, uv, pip requirements, Poetry, Cargo, Go
  modules, Bundler, and Composer lockfiles. It does not parse `yarn.lock` or
  `Pipfile.lock` yet.
- OSV and Canonical feeds need network access. Results are cached in the state
  directory. A failed source does not stop cleanup; it is recorded as failed or
  partial.
- The scan does not replace Ubuntu Pro Livepatch. Livepatch can apply kernel
  fixes without a reboot when Canonical publishes them. This scan reports
  affected packages before a fix exists.

Run only the security scan from the installed copy:

```bash
~/.local/libexec/linux-cleanup-service/linux-cleanup-service ~/.local/libexec/linux-cleanup-service/security.py
```

From a checkout, run `python3 src/security.py`. The command prints the summary
and writes `security.json`.

## Risk Levels

Low-risk cleanup runs without a prompt:

- uv, npm, and Yarn caches.
- Brave cache above `BRAVE_CACHE_MAX_SIZE`, only when Brave is stopped.
- Zed cache, and old Zed remote servers. The newest two servers remain.
- Old Homebrew versions and downloads on macOS; the APT package cache on Linux.
- Old, unused `/tmp` entries that this user owns. Credential, socket, session,
  and agent entries are never deleted.
- Archived journals above `JOURNAL_WARN_SIZE` on Linux.
- Stopped Docker containers and Docker build cache older than 24 hours.
- Tool caches in projects: `.venv`, `__pycache__`, `.pytest_cache`,
  `.ruff_cache`, and `.mypy_cache`, after `PROJECT_CACHE_MAX_AGE_HOURS`.

High-risk cleanup needs a recorded decision, and the prompt appears only when
the system is short of disk space or memory:

- Zed saved data, which can contain settings, history, and threads.
- Unreferenced Hugging Face model downloads.
- Downloaded Playwright browsers.
- Node dependencies and build folders in `PROJECT_ROOT`, grouped by project.
- Docker images that no container uses, and unused Docker networks.

Project cleanup touches only folders with these exact names: `node_modules`,
`.venv`, `__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `dist`,
`.next`, `.nx`, and `coverage`. It never deletes a folder that Git tracks.
It deletes `dist`, `.next`, `.nx`, and `coverage` only when Git ignores them,
because unignored build output can be hand-written code. A project with a
running program inside it is skipped.

## Safety and Limits

**Warning:** Cleanup permanently deletes data. Low-risk cleanup deletes without
a prompt. Keep independent backups and review configured paths and thresholds.

Every deletion skips data that a program of the same user references. On Linux,
the check reads `/proc`. If a process cannot be inspected at all, the pass
deletes nothing that depends on the check. On macOS, the check uses `lsof`.
Docker volumes are never pruned.

Automatic upgrades replace installed software without asking. Homebrew's
automatic post-install cleanup is disabled; `brew cleanup` runs as a low-risk
action instead.

Security findings are advisory. The pass does not upgrade project dependencies,
remove affected packages, or reboot the machine. Review and apply those changes
yourself. A reported reboot remains pending until you reboot.


The installer does not change journal retention limits. On Linux it installs
narrowly scoped passwordless sudo commands for updates, APT cache cleanup, and
journal vacuuming. This is privileged access, not a sandbox. Accounts without
the sudo rule set `SUDO_TASKS_ENABLED=false`.

## Update and Uninstall

Pull the repository and run `./scripts/install` again to update installed code.
Review new settings before updating. Existing configuration is preserved.

To remove the service, code, and startup hook:

```bash
./scripts/uninstall
```

Configuration and state remain. The runtime copy is separate from the checkout,
so removing the checkout does not remove the service. Clone again when you
need the installer or uninstaller.

## Optional Proton Pass SSH Agent

Linux desktop users can install a native Proton Pass SSH agent separately.
The integration uses the encrypted desktop keyring for persistent login and
waits for that keyring to unlock before starting. It does not export SSH private
keys. See [Proton Pass setup](docs/proton-pass.md).

## More Information

- [macOS commands](macos/README.md)
- [Operations](docs/operations.md)
- [Architecture](docs/architecture.md)
- [Design decisions](docs/decisions.md)
- [Development](docs/development.md)

## License

[MIT](LICENSE).
