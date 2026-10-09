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

## Select Optional Actions

Run `./scripts/configure` to select actions. The menu replaces the previous
optional-action selection and preserves thresholds and other settings.
Alternatively, edit the flags below. Every flag defaults to `false`.
Cleanup flags enable previews; they never bypass the Y/N deletion prompt.

| Flag | Action |
| --- | --- |
| `UV_CLEANUP_ENABLED` | Prune the UV cache. |
| `BRAVE_CLEANUP_ENABLED` | Clear oversized Brave cache when Brave is stopped. |
| `NPM_CLEANUP_ENABLED` | Verify and clear the npm cache. |
| `YARN_CLEANUP_ENABLED` | Clear the Yarn cache, or prune old entries when Yarn is unavailable. |
| `PLAYWRIGHT_CLEANUP_ENABLED` | Remove old downloaded browsers that are not in use. |
| `ZED_CLEANUP_ENABLED` | Remove old Zed data and cache entries that are not in use. |
| `ZED_SERVER_CLEANUP_ENABLED` | Remove old remote servers while retaining the newest installations. |
| `HUGGINGFACE_CLEANUP_ENABLED` | Remove old orphaned model blobs and incomplete downloads. |
| `PROJECT_ARTIFACTS_CLEANUP_ENABLED` | Remove old dependencies and build output beneath `PROJECT_ROOT`. |
| `DOCKER_PRUNE_ENABLED` | Prune unused Docker resources in the selected context; preserve volumes. |
| `TMP_CLEANUP_ENABLED` | Remove old user-owned entries beneath `TMP_DIR`. |
| `APT_CACHE_CLEANUP_ENABLED` | Clear downloaded APT packages on Linux. |
| `JOURNAL_CLEANUP_ENABLED` | Vacuum oversized system and user journals on Linux. |
| `BREW_CLEANUP_ENABLED` | Remove old Homebrew versions and downloads on macOS. |
| `RUNTIME_UPDATES_ENABLED` | Automatically update verified Node.js and Bun releases on macOS. |

**Warning:** Zed data cleanup includes persistent application state.
Project cleanup includes `node_modules`, `.venv`, `dist`, `.next`, `.nx`,
`coverage`, and selected Python cache directories. Choose these actions only
when you accept their deletion scope.

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
APT cache cleanup currently requires APT updates to be enabled for the account.

## Cleanup Thresholds

| Setting | Default | Meaning |
| --- | --- | --- |
| `UV_CACHE_MAX_SIZE` | `1GiB` | Size threshold for UV pruning. |
| `BRAVE_CACHE_MAX_SIZE` | `2GiB` | Size threshold for Brave cache clearing. |
| `NPM_CACHE_WARN_SIZE` | `1GiB` | npm cleanup size threshold. |
| `CACHE_MAX_AGE_HOURS` | `24` | Age threshold for selected application caches. |
| `UV_CACHE_CLEAN_DAILY` | `true` | Prune UV on each approved pass when selected. |
| `UV_CACHE_MAX_AGE_HOURS` | `24` | UV age threshold. |
| `NPM_CACHE_CLEAN_DAILY` | `true` | Clear npm on each approved pass when selected. |
| `NPM_CACHE_MAX_AGE_HOURS` | `24` | npm age threshold. |
| `ZED_SERVER_KEEP_COUNT` | `2` | Number of newest remote server installs retained. |
| `PROJECT_ARTIFACTS_MAX_AGE_HOURS` | `24` | Project artifact age threshold. |
| `DOCKER_PRUNE_MAX_AGE_HOURS` | `24` | Docker prune age filter. |
| `TMP_MAX_AGE_HOURS` | `24` | Temporary entry age threshold. |
| `JOURNAL_WARN_SIZE` | `100MiB` | Journal warning and vacuum trigger. |
| `JOURNAL_SYSTEM_MAX_USE` | `80M` | Target for approved journal vacuuming. |
| `COMMAND_TIMEOUT_SECONDS` | `900` | External command timeout. |

Turning off a daily-cache flag does not disable that integration. Size or age
thresholds can still trigger cleanup after approval. Use the integration's
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
Runtime upgrades are disabled until selected. Once enabled, they do not
require cleanup approval. Node.js stays on the configured major line; Bun uses
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
