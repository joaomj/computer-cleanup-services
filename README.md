# computer-cleanup-services

Daily maintenance for Linux and macOS. System upgrades run automatically.
Cleanup deletions require approval in an interactive terminal.

One codebase supports two installed services:

- Linux: `linux-cleanup.service`, stored under `linux-cleanup-service`.
- macOS: `com.user.macos-cleanup-service`, stored under `macos-cleanup-service`.

## How It Works

The first interactive shell of the day offers cleanup for the actions you
selected. The shell shows enabled scopes and a preview, then waits for Y/N.
Only Y authorizes cleanup. Enter, N, closed input, or no terminal means no
deletion. There is no unattended approval flag.

After the cleanup decision, the shell starts automatic upgrades in the
background. Update and cleanup records have separate daily gates.
If maintenance is already running, cleanup waits for a later shell or a
manual request; it does not interrupt the running job.

Application-specific maintenance is opt-in. Nothing assumes that you use
Brave, Zed, Hugging Face, Docker, or a particular developer tool.
APT and Snap updates on Linux and Homebrew updates on macOS remain enabled
when the relevant tools are available. Verified Node.js and Bun upgrades are
optional; once selected, they run automatically.

## Requirements

- Python 3.10 or later on `PATH`, Bash, and Git.
- Linux with a systemd user manager, or macOS with launchd.
- An interactive Bash or zsh shell for the startup hook.
- Tools for the optional actions you select.
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
service, and adds a shell startup hook. It offers an optional-action menu.
Choose only the applications and cleanup scopes you want to manage.
Choosing an action enables previews; it does not authorize future deletions.

If you skip the menu, optional actions stay disabled on a new installation.
Existing configuration is preserved. Change the selection later with:

```bash
./scripts/configure
```

Open a new shell to request maintenance. Shell startup waits for a cleanup
response when cleanup actions are enabled. Upgrades run in the background
and do not require cleanup approval.

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

## Inspect or Request Cleanup

From a checkout, preview enabled actions without upgrades or cleanup deletions:

```bash
python3 src/maintenance.py --dry-run --force
```

Request cleanup and answer its Y/N prompt:

```bash
python3 src/maintenance.py --cleanup-only --force
```

On Linux, inspect update and cleanup results:

```bash
~/.local/libexec/linux-cleanup-service/python3 ~/.local/libexec/linux-cleanup-service/status.py --json
~/.local/libexec/linux-cleanup-service/python3 ~/.local/libexec/linux-cleanup-service/status.py --cleanup-json
```

On macOS, replace `linux-cleanup-service` with `macos-cleanup-service`.
A dry run can create the private state directory and lock file. It does not
run upgrades, cleanup commands, or journal vacuuming.

## Safety and Limits

**Warning:** Approved cleanup can permanently delete data. Zed cleanup includes
its data directory. Project cleanup can remove dependencies and build output.
Temporary-file cleanup removes old user-owned entries. Docker cleanup removes
unused containers, images, build cache, and networks, but preserves volumes.
Keep independent backups and review configured paths before approving cleanup.

The preview provides scopes and estimates, not a fixed file inventory.
Cleanup checks eligibility again after approval; new eligible entries can be
included. Cleanup tools can remove more than an age-based file preview shows.

Automatic upgrades replace installed software without asking. The approval
policy covers explicit cleanup, not files a package manager replaces during
an upgrade. Homebrew's automatic post-install cleanup is disabled; explicit
`brew cleanup` requires selection and approval.

The installer does not change journal retention limits or vacuum journals.
On Linux it installs narrowly scoped passwordless sudo commands for updates
and approved cache/journal cleanup. This is privileged access, not a sandbox.

This repository is still being prepared for public distribution. CLI approval
and isolated installation checks are included. Native service registration and
Linux host operation still need release validation.

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

## More Information

- [macOS commands](macos/README.md)
- [Operations](docs/operations.md)
- [Architecture](docs/architecture.md)
- [Design decisions](docs/decisions.md)
- [Development](docs/development.md)

## License

[MIT](LICENSE).
