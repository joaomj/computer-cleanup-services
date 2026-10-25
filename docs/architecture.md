# Architecture

Maintenance uses one Python codebase and an OS-aware shell installer.
The installed service names remain platform-specific.

```text
03:00 timer, or first shell of the day
  |
  +--> systemd user service / launchd user job
         +--> maintenance.py (daily pass, once per day)
                +--> APT/Snap on Linux or Homebrew on macOS
                +--> optional verified Node.js/Bun upgrades on macOS
                +--> security scan -> security.json
                +--> low-risk cleanup, no prompt (caches, temp, tool caches)
                +--> high-risk discovery -> pending-review.json
                +--> status.json

interactive shell, when pending-review.json exists
  |
  +--> maintenance.py --review (once per day)
         +--> pressure gate: disk, memory, or swap near full
         +--> y/N/q per item
         +--> decisions.json, review-status.json
         +--> re-validated deletion of approved items
```

## Responsibilities

- `config.py` validates settings and generates scoped Linux sudo rules.
- `configure.py` selects actions.
- `maintenance.py` owns command-line modes, daily gates, and the review prompt.
- `cleanup.py` runs upgrades, the security scan, and low-risk cleanup, then
  calls discovery.
- `security.py` combines the security sources, filters findings, checks reboot
  state, and writes `security.json`.
- `oval.py` evaluates Canonical OVAL definitions with OpenSCAP for Ubuntu OS
  packages.
- `osv.py` discovers project lockfiles and queries OSV for dependency
  vulnerabilities.
- `homebrew_vulns.py` reads Homebrew findings from `brew vulns`.
- `cache_cleanup.py` owns low-risk application cache cleanup.
- `artifacts.py` owns project folder rules and automatic tool-cache cleanup.
- `high_risk.py` finds high-risk candidates and deletes approved ones after a
  new check.
- `decisions.py` records decisions and the pending-review list.
- `common.py` owns commands, the process-reference check, and tree inspection.
- `runtime_update.py` verifies and installs optional runtime upgrades.
- `state.py` owns the process lock and atomic private state writes.
- `pressure.py` decides whether the machine needs space before a review.
- `status.py` reports the daily pass and review records.

A shared exclusive lock prevents a review and a daily pass from running at the
same time. A review that meets a running pass exits silently.

A high-risk candidate has a stable identity: the project folder, the data entry,
the Hugging Face model, or the Docker image ID. The decision record stores the
identity, answer, and size. A kept candidate is offered again only when its size
is more than 125% of the recorded size.

Before each high-risk deletion, the code captures running-program references
again and checks ownership, age, special files, and Git state for every target.
A target that fails a check is kept and reported.

## Security Scan

The security scan runs after package upgrades. It collects findings from three
sources:

- Ubuntu OVAL definitions for installed OS packages and the kernel. The code
  downloads the feed for the running release, evaluates it with `oscap`, and
  keeps installed-package results.
- OSV for lockfiles under `PROJECT_ROOT`. Supported files include npm, pnpm,
  uv, pip requirements, Poetry, Cargo, Go modules, Bundler, and Composer.
- Homebrew for installed formulae on macOS through `brew vulns --json`.

Findings below `SECURITY_MIN_SEVERITY` are removed before the code writes
`security.json`. The record includes source summaries, a reboot status, and a
combined finding list. The shell status reads the compact summary stored in
`status.json`.

Linux reboot detection uses `/var/run/reboot-required` and compares the running
kernel release with the newest installed `linux-image-*` package. The scan does
not reboot the machine.

Network and scanner failures are recorded per source. A failed source marks the
overall record `failed` or `partial`; it does not stop cleanup. OSV vulnerability
details and the OVAL feed are cached in the state directory.

## Installation Boundary

The installer copies runtime files outside the checkout and records the selected
Python interpreter in `python3-interpreter`. A launcher named after the service
runs that interpreter and falls back to a Homebrew Python when an upgrade
deletes the recorded path. The shell hooks and service both use the launcher,
and macOS names the background item after it. Removing the checkout does not
affect the installed copy.

Services run as the user. Linux updates and root-owned cache and journal
operations use exact commands in a scoped sudoers rule. No general root shell
is granted. Installation does not change journal retention or delete journals.

## Optional Proton Pass Agent

`scripts/install-proton-pass` installs a separate Linux user service and Bash
hooks. `proton_pass_agent.py` waits for the default Secret Service collection to
unlock and for the Proton API to answer, then replaces its process with the
native Proton Pass SSH agent.
The service starts with the graphical session and uses persistent D-Bus keyring
storage. Its lifecycle is independent of maintenance.

`proton-pass-sign.sh` selects the native agent socket for SSH commit signing.
Git settings and public-key selectors remain local configuration, not repository
data. The installer preserves an existing GitHub configuration file.
