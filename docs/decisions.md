# Design Decisions

## Keep One Codebase and Two User Services

Detect the OS in the installer and Python code. Use systemd on Linux and
launchd on macOS. Keep the installed names `linux-cleanup-service` and
`macos-cleanup-service`; the shared project is `computer-cleanup-services`.

## Split Cleanup by Risk

Low-risk data is regenerable and cheap to restore. The daily pass deletes it
without a prompt, because a daily prompt for caches trains users to approve
without reading.

High-risk data can contain application state or take long to restore. The daily
pass only lists it. A terminal review asks once per item and records the answer,
so the same item does not return every day. The review runs only when the
machine is short of disk space, memory, or swap. With free space, a large
deletion has no benefit and the prompt only adds noise. Pending items stay
recorded and are offered when pressure appears. A kept item returns after it grows
by more than 25%, because its cost to the disk has changed.

Approval applies to a specific item, not to a category. The code checks the
item again before deletion, because state can change between the offer and
the answer.

## Never Delete Code as a Project Artifact

Project cleanup matches exact folder names and never descends into `.git`.
It never deletes a folder that Git tracks. It deletes build-output names only
when Git ignores them, because a folder named `dist` can contain hand-written
code.

Tool caches (`.venv`, `__pycache__`, `.pytest_cache`, `.ruff_cache`,
`.mypy_cache`) are regenerable and cheap, so the daily pass deletes them without
a prompt. `node_modules` and build output can take long to restore or can hold
hand-written code, so they need a recorded decision.

## Turn On Every Cleanup Action by Default

All cleanup actions are on in a new installation. Missing tools are skipped.
The risk split, not the action selection, protects stateful data.

## Preserve Docker Volumes and the Selected Context

Use the user's Docker context. Do not silently switch to a host daemon.
Keep age filters and report unsupported filters instead of retrying without
limits. Do not prune volumes.

## Keep Public Defaults Simple

Use `.conf` templates with the existing `KEY=VALUE` format. Configuration does
not execute shell code and requires no third-party parser.
Keep credentials and machine-specific settings outside the repository.

## Use Scoped Linux Sudo Access

Allow exact APT, Snap, and journal commands for the current user.
The service uses non-interactive sudo and reports inaccessible tasks as skipped.
Do not install automatic journal retention limits.

## Use Vendor Security Data per Platform

Use Canonical OVAL definitions for Ubuntu OS packages. They include Ubuntu
package versions, backported fixes, and CVEs that still need evaluation. A
generic CVE feed can mark a package vulnerable when Ubuntu has already fixed it,
or miss an unfixed CVE that Ubuntu tracks. OpenSCAP evaluates the definitions
against installed packages.

Use OSV for project lockfiles. It supports the package ecosystems in this
repository and returns the exact versions that a lockfile records. OSV does not
provide a usable Homebrew ecosystem, so macOS reads `brew vulns`, the native
Homebrew command backed by OSV.

Do not treat a finding as a repair instruction. The daily pass records findings
and a required reboot. It does not upgrade project dependencies, remove OS
packages, or reboot the machine. Those actions change application behavior and
need a person to choose the time.

## Verify Optional Runtime Upgrades

For selected Node.js and Bun upgrades, require signed manifests, matching hashes,
valid runtime versions, and a minimum release age. Install versioned directories
and switch runtime links atomically. Keep old directories for rollback.
Homebrew's weekly upgrade interval limits frequency but does not guarantee
release age for individual packages.
