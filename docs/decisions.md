# Design Decisions

## Keep One Codebase and Two User Services

Detect the OS in the installer and Python code. Use systemd on Linux and
launchd on macOS. Keep the installed names `linux-cleanup-service` and
`macos-cleanup-service`; the shared project is `computer-cleanup-services`.

## Separate Automatic Upgrades from Approved Cleanup

Background services cannot ask questions in a terminal. Run upgrades in the
background, and run cleanup approval in the interactive shell.
Do not provide an unattended deletion-approval flag.
Keep separate daily records so declining cleanup does not disable upgrades.

Approval covers explicit cleanup operations. Automatic upgrades still replace
software files. Disable Homebrew's automatic post-install cleanup so explicit
cache and old-version cleanup remains behind the approval boundary.

## Let Users Select Their Applications

Disable optional application maintenance by default. Provide a selection menu
and plain configuration flags. Selecting an action enables previews, not
standing permission to delete data.

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

## Verify Optional Runtime Upgrades

For selected Node.js and Bun upgrades, require signed manifests, matching hashes,
valid runtime versions, and a minimum release age. Install versioned directories
and switch runtime links atomically. Keep old directories for rollback.
Homebrew's weekly upgrade interval limits frequency but does not guarantee
release age for individual packages.
