# Unattended daily Linux cleanup

This opt-in profile is for the mini PC. It runs separately from the standard
interactive maintenance service. Installing it authorizes daily deletion
without a terminal prompt. It does not change the standard service's approval
policy or install the shell startup hook.

## Scope

The installer enables one system timer for each regular account with a home
at `/home/<username>`. Each service runs as that account, never root.

| Scope | Daily action |
| --- | --- |
| `/tmp` | Remove user-owned entries only when all contents have been unchanged for at least 24 hours. |
| `~/projects/**/node_modules` | Remove dependency directories only when all contents have been unchanged for at least 24 hours and the workspace has no observed active job. |
| `~/.cache/uv` | Remove cache contents when no observed process uses them and no UV job or existing cache lock blocks cleanup. Preserve the cache lock file. |
| `~/.cache/git` and `~/.cache/git-clones` | Remove contents of these explicitly disposable cache locations when no observed process uses them and no Git job is running. |

Missing cache directories are ignored. UV's cached Git downloads are covered
by UV-cache cleanup. The profile does not search for arbitrary Git clones or
run Git garbage collection. Repository `.git` data outside the designated
Git/UV cache locations is not a cleanup target.

The profile does not clean npm, Pi installations, Pi history, container storage,
Python environments, build output, or application data. Workspace discovery
skips `.git`, `.venv`, `.pi`, and directory symlinks. Other workspace locations
and custom XDG cache locations are not included in this initial profile.

## Safety and limits

Warning: Installation authorizes permanent unattended deletion. Dependencies
and caches will need downloads or regeneration. Keep source changes and
important data outside disposable locations, and maintain independent backups.

Temporary-file cleanup excludes Pi runtime directories, known session/socket
paths, repository metadata, and names associated with credentials. It rejects
trees containing special files or entries owned by another account. Name checks
cannot identify every secret; do not store credentials in disposable directories.

The cleaner inspects same-account process working directories, executable files,
open handles, memory mappings, and absolute command arguments. It also preserves
workspaces associated with those paths. Inspection failures stop cleanup with
an error, except for the known session services `systemd`, `(sd-pam)`,
`ssh-agent`, and `gpg-agent`, which deliberately restrict inspection.

Ownership, age, and process use are checked again before deletion. These checks
reduce interference but are not a transaction with applications: a process can
start or access files after the check. Do not use this profile for dependencies
that must remain available to an unobserved external job. New Pi container
sessions will need an explicit lifecycle lock before their storage is included.

## Install

Requirements: Linux, systemd, Python 3.10 or later, and administrator access.
The bare profile assumes homes at `/home/<username>` and workspaces under
`~/projects`.

Preview the current account without deleting files or writing state:

```bash
python3 src/daily_cleanup.py --dry-run
```

Review the script and policy, then install:

```bash
sudo ./scripts/install-daily
```

The installer copies a root-owned script to `/opt/linux-daily-cleanup`, installs
the system unit templates, and previews every selected account before enabling
any new cleanup timer. A preview error stops installation. Earlier file copies
are not rolled back. Reinstallation replaces the same files and timers; it does
not create duplicate schedules.

Cleanup runs at 03:00–03:20 local time. Persistent timers request catch-up after
a missed schedule; enabling a timer can therefore result in cleanup outside
that window. The installer does not request an immediate cleanup service run.

The installer writes `config/daily-apt.conf` to
`/etc/apt/apt.conf.d/99linux-daily-updates`. It expands the native unattended-update
policy to include normal Ubuntu release, security, and updates archives; ESM
security and updates archives where available; GitHub CLI; and Tailscale.
The allowlist uses the repository origins verified on this mini PC. Brave and
other third-party repositories are not included. Future repositories need
explicit policy changes. Ubuntu proposed and backports archives are not included.

The policy replaces the existing unattended origin allowlists. Package holds
and blacklists remain unchanged, and the profile adds no phased-rollout override.
Automatic reboots and general unused-dependency removal are disabled. Kernel
retention remains governed by the existing Ubuntu policy. Updates can restart
services, including Tailscale, and interrupt connections. Availability of an
update does not guarantee that unattended-upgrades can install it immediately;
dependency resolution and configuration-file prompts can defer it.

APT uses its native daily timers; the installer does not request an immediate
package upgrade. Snap keeps its existing native automatic refresh schedule.
Cleanup does not run competing APT or Snap commands and does not upgrade host
Node, npm, or Pi. This installer does not uninstall applications; removal of
Brave on this host is a separate administrator action.

## Inspect and disable

For the `minipc` account:

```bash
systemctl list-timers --all 'linux-daily-cleanup@*'
journalctl -u linux-daily-cleanup@minipc.service
sudo cat /var/lib/linux-daily-cleanup/minipc/status.json
```

Each account has a separate lock and a private status record. The status reports
`running`, `success`, or `failed`. A terminated process can leave `running` behind;
check the service result in that case. Deletion progress is written to the
journal. Byte counts are estimates, not guaranteed reclaimed physical space.

Stop future cleanup and any current pass for an account:

```bash
sudo systemctl disable --now linux-daily-cleanup@minipc.timer
sudo systemctl stop linux-daily-cleanup@minipc.service
```

Repeat for the other installed accounts. The standard `scripts/uninstall` does
not remove this separate opt-in profile. Disabling it does not disable APT or
Snap updates and does not recover deleted files. To revert only the expanded
APT origin policy, remove `/etc/apt/apt.conf.d/99linux-daily-updates`; the native
Ubuntu policy files remain in place.
