# Architecture

The project has one Python codebase and an OS-aware shell installer.
The installed service names remain platform-specific.

```text
interactive shell
  |
  +--> maintenance.py --cleanup-only
  |      +--> separate daily cleanup decision
  |      +--> enabled scopes and cleanup preview
  |      +--> terminal Y/N prompt
  |      +--> approved cleanup, or no deletion
  |
  +--> systemd user service / launchd user job
         +--> maintenance.py --updates-only
                +--> daily upgrade gate
                +--> APT/Snap on Linux or Homebrew on macOS
                +--> optional verified Node.js/Bun upgrades on macOS
                +--> status.json
```

## Responsibilities

- `config.py` validates settings and generates scoped Linux sudo rules.
- `configure.py` selects optional actions without authorizing deletions.
- `maintenance.py` owns command-line modes, daily gates, and terminal approval.
- `cleanup.py` runs selected update and cleanup operations.
- `cache_cleanup.py` owns application cache cleanup.
- `runtime_update.py` verifies and installs optional runtime upgrades.
- `state.py` owns the process lock and atomic private state writes.
- `status.py` reports update and cleanup records.

A shared exclusive lock prevents simultaneous maintenance passes.
`status.json` records upgrades. `cleanup-status.json` records cleanup decisions.
Declining cleanup consumes the day's cleanup decision, not the update gate.
A missing terminal does not consume the next interactive cleanup decision.
Use `--force` for an explicit retry on the same day.

Background services always use `--updates-only`. There is no CLI flag that
approves unattended cleanup. The interactive path requires terminal input and
output, previews the selected scopes, and accepts only Y/yes as approval.
The operation runner also defaults cleanup mutations to inspection-only.

## Installation Boundary

The installer copies runtime files outside the checkout and records the selected
Python interpreter through an installed symlink. The shell hooks and service
use that interpreter. Removing the checkout does not affect the installed copy.

Services run as the user. Linux updates and approved root-owned cache/journal
operations use exact commands in a scoped sudoers rule. No general root shell
is granted. Installation does not change journal retention or delete journals.
