# Configuration Reference

The installer copies `config/default.conf` on Linux or `config/macos.conf`
when no user configuration exists. Both templates use simple `KEY=VALUE`
lines. They contain public defaults, not credentials.

Installed configuration is named `environment`:

- Linux: `~/.config/linux-cleanup-service/environment`
- macOS: `~/Library/Application Support/macos-cleanup-service/environment`

`XDG_CONFIG_HOME` overrides the configuration root when set. Process variables
override file values. Values are not evaluated as shell code.
Use absolute paths or paths that begin with `~` for configurable directories.

## Select Actions

Run `./scripts/configure` to select actions. The menu replaces the previous
action selection and preserves thresholds and other settings.
Alternatively, edit the flags below. Every cleanup flag defaults to `true`.
`RUNTIME_UPDATES_ENABLED` defaults to `false`.

| Flag | Low-risk action (automatic) | High-risk action (recorded decision) |
| --- | --- | --- |
| `UV_CLEANUP_ENABLED` | Prune the UV cache. | None. |
| `BRAVE_CLEANUP_ENABLED` | Clear oversized Brave cache when Brave is stopped. | None. |
| `NPM_CLEANUP_ENABLED` | Verify and clear the npm cache. | None. |
| `YARN_CLEANUP_ENABLED` | Clear the Yarn cache. | None. |
| `PLAYWRIGHT_CLEANUP_ENABLED` | None. | Remove old downloaded browsers. |
| `ZED_CLEANUP_ENABLED` | Prune old Zed cache entries. | Remove old Zed saved-data entries. |
| `ZED_SERVER_CLEANUP_ENABLED` | Remove old remote servers; keep the newest. | None. |
| `HUGGINGFACE_CLEANUP_ENABLED` | None. | Remove unreferenced model downloads. |
| `PROJECT_ARTIFACTS_CLEANUP_ENABLED` | Remove tool caches in `PROJECT_ROOT`. | Remove `node_modules` and build folders in `PROJECT_ROOT`. |
| `DOCKER_PRUNE_ENABLED` | Prune stopped containers and build cache. | Remove unused images and networks. |
| `TMP_CLEANUP_ENABLED` | Remove old user-owned entries in `TMP_DIR`. | None. |
| `APT_CACHE_CLEANUP_ENABLED` | Clear downloaded APT packages on Linux. | None. |
| `JOURNAL_CLEANUP_ENABLED` | Vacuum oversized journals on Linux. | None. |
| `BREW_CLEANUP_ENABLED` | Remove old Homebrew versions and downloads on macOS. | None. |
| `RUNTIME_UPDATES_ENABLED` | Update verified Node.js and Bun on macOS. | None. |

`SUDO_TASKS_ENABLED` (default `true`) controls APT, Snap, APT cache, and system
journal tasks on Linux. Set it to `false` for accounts without the scoped sudo rule.

## Automatic Package Upgrades

| Setting | Default | Meaning |
| --- | --- | --- |
| `APT_UPDATES_ENABLED` | `true` | Refresh APT metadata and run `apt-get upgrade -y`. |
| `SNAP_UPDATES_ENABLED` | `true` | Run `snap refresh`. |
| `BREW_UPDATES_ENABLED` | `true` | Refresh Homebrew metadata and upgrade packages. |
| `BREW_UPGRADE_MIN_AGE_DAYS` | `7` | Minimum interval between Homebrew upgrades. |

APT and Snap use non-interactive `sudo`. Homebrew runs as the user.
Homebrew upgrades at most weekly; the interval is not a minimum package release
age. The service disables Homebrew's automatic post-install cleanup.

## Cleanup Thresholds

| Setting | Default | Meaning |
| --- | --- | --- |
| `UV_CACHE_MAX_SIZE` | `1GiB` | Size threshold for UV pruning. |
| `BRAVE_CACHE_MAX_SIZE` | `2GiB` | Size threshold for Brave cache clearing. |
| `NPM_CACHE_WARN_SIZE` | `1GiB` | npm cleanup size threshold. |
| `CACHE_MAX_AGE_HOURS` | `24` | Age threshold for selected application caches. |
| `UV_CACHE_CLEAN_DAILY` | `true` | Prune UV on each daily pass when selected. |
| `UV_CACHE_MAX_AGE_HOURS` | `24` | UV age threshold. |
| `NPM_CACHE_CLEAN_DAILY` | `true` | Clear npm on each daily pass when selected. |
| `NPM_CACHE_MAX_AGE_HOURS` | `24` | npm age threshold. |
| `ZED_SERVER_KEEP_COUNT` | `2` | Number of newest remote server installs retained. |
| `PROJECT_ARTIFACTS_MAX_AGE_HOURS` | `24` | Age threshold for `node_modules` and build folders. |
| `PROJECT_CACHE_MAX_AGE_HOURS` | `1` | Age threshold for tool caches such as `.venv` and `__pycache__`. |
| `HIGH_RISK_REVIEW_DISK_PERCENT` | `85` | Offer high-risk cleanup when the disk reaches this used percentage. |
| `HIGH_RISK_REVIEW_MEMORY_PERCENT` | `10` | Offer high-risk cleanup when available memory falls below this percentage. Set to `0` to disable. |
| `DOCKER_PRUNE_MAX_AGE_HOURS` | `24` | Docker prune age filter. |
| `TMP_MAX_AGE_HOURS` | `24` | Temporary entry age threshold. |
| `JOURNAL_WARN_SIZE` | `100MiB` | Journal warning and vacuum trigger. |
| `JOURNAL_SYSTEM_MAX_USE` | `80M` | Target for journal vacuuming. |
| `COMMAND_TIMEOUT_SECONDS` | `300` | External command timeout. |

Age thresholds accept `0`, which means no minimum age. Swap use at 90% or
more also offers high-risk cleanup. `HIGH_RISK_REVIEW_DISK_PERCENT=0` offers
high-risk cleanup on every review, whatever the free space. Set
`HIGH_RISK_REVIEW_DISK_PERCENT` above `100` and
`HIGH_RISK_REVIEW_MEMORY_PERCENT` to `0` to disable the pressure gate.
Turning off a daily-cache flag does not disable that integration. Size or age
thresholds can still trigger cleanup. Use the integration's
`*_CLEANUP_ENABLED` flag to disable it.

The size parser accepts `B`, `KB`, `KiB`, `MB`, `MiB`, `GB`, `GiB`, `TB`,
and `TiB`. Journal targets use journal units such as `80M`.
The installer does not enforce automatic journal retention.

## Paths

| Setting | Default |
| --- | --- |
| `UV_CACHE_DIR` | `~/.cache/uv`, or `XDG_CACHE_HOME/uv` |
| `BRAVE_CACHE_PATH` | Brave under the platform cache root |
| `NPM_CACHE_PATH` | `~/.npm` |
| `YARN_CACHE_PATH` | Yarn under the platform cache root |
| `PLAYWRIGHT_CACHE_PATH` | `ms-playwright` under the platform cache root |
| `ZED_DATA_PATH` | `~/.local/share/zed`, or `XDG_DATA_HOME/zed` |
| `ZED_CACHE_PATH` | Zed under the platform cache root |
| `HUGGINGFACE_CACHE_PATH` | `~/.cache/huggingface`, or `XDG_CACHE_HOME/huggingface` |
| `ZED_SERVER_PATH` | `~/.zed_server` |
| `PROJECT_ROOT` | `~/projects` |
| `TMP_DIR` | `/tmp` |

The platform cache root is `~/.cache` on Linux and `~/Library/Caches` on macOS,
unless `XDG_CACHE_HOME` is set. Check the application paths for your installation
before enabling cleanup; application layouts can differ.

## Optional Runtime Upgrades

The optional Node.js and Bun updater currently supports Apple Silicon macOS.
Runtime upgrades are disabled until selected. Once enabled, they run in the
daily pass. Node.js stays on the configured major line; Bun uses
stable releases. The updater checks signed manifests, archive hashes, and
runtime versions before changing links.

| Setting | Default |
| --- | --- |
| `RUNTIME_NODE_MAJOR` | `24` |
| `RUNTIME_MIN_RELEASE_AGE_DAYS` | `7` |
| `RUNTIME_INSTALL_ROOT` | `~/.local/opt` |
| `RUNTIME_BIN_DIR` | `~/.local/bin` |
| `RUNTIME_NETWORK_TIMEOUT_SECONDS` | `30` |
| `RUNTIME_GPGV_PATH` | Auto-detect |
| `RUNTIME_GPG_PATH` | Auto-detect |

Ensure the runtime binary directory is on your shell's `PATH` if you want to
use these installations. Existing runtime versions remain available for rollback.
