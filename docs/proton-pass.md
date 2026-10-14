# Proton Pass SSH agent on Linux

This optional integration runs Proton Pass as the SSH agent. It does not export
SSH private keys, create vault items, or register keys with remote services.
The maintenance installer does not install this integration automatically.

## Requirements

- Linux with a systemd graphical user session and Python 3.10 or later.
- Proton Pass CLI with `ssh-agent start` support, installed on `PATH`.
- A D-Bus Secret Service with an unlocked default collection, such as GNOME Keyring.
- SSH keys already stored as SSH key items in Proton Pass.

The CLI defaults to a Linux kernel keyring that does not survive reboot.
This integration selects `PROTON_PASS_KEY_PROVIDER=keyring` and
`PROTON_PASS_LINUX_KEYRING=dbus` for both Bash and the service.
The encrypted desktop keyring stores Proton's local session-encryption key.
Proton retains its normal local session data. This integration does not use
the plaintext filesystem-key backend.

## Install

Run as your regular desktop user:

```bash
./scripts/install-proton-pass
. "$HOME/.local/libexec/proton-pass-ssh-agent/proton-pass-init.sh"
pass-cli login
```

Authenticate only after selecting the D-Bus backend. When changing backends
with an existing authenticated session, consult Proton's storage documentation
before logging out or replacing local session data.

The installer adds a shell hook without duplicates to `.profile` and `.bashrc`.
For another shell, set the same three environment variables using that shell's
syntax. It installs the service without starting it, so login can finish first.
Rerun the installer after moving the CLI executable or updating this integration.
Then restart `proton-pass-ssh-agent.service` to load the installed changes.
Reinstallation preserves your GitHub identity selection.

Add this line at the beginning of `~/.ssh/config`:

```sshconfig
Include ~/.ssh/proton-pass-github.conf
```

Remove other `IdentityFile` settings that apply to `github.com` if you want no
local-private-key fallback. The installer preserves your main SSH configuration.
The included file selects the Proton socket explicitly, independent of another
agent selected by a terminal or IDE. Other hosts retain their SSH settings.
The shell hook selects Proton as the default agent for other SSH tools.

The initial GitHub configuration offers all agent keys. Select one key to avoid
server authentication limits and prevent another vault key from being offered.
After starting the agent, use `ssh-add -L` to identify its public-key entry.
Save only that entry to `~/.ssh/github.pub`. In
`~/.ssh/proton-pass-github.conf`, replace the identity settings with:

```sshconfig
    IdentityFile ~/.ssh/github.pub
    IdentitiesOnly yes
```

A public-key selector does not contain a private key. Register it as an account
SSH authentication key on GitHub for account-wide access. A repository deploy
key does not grant account-wide access. Preserve existing host-key checks.

Disable any previous Proton key-loading service before starting the native agent.
Do not run `pass-cli ssh-agent daemon start` alongside the systemd service.

```bash
systemctl --user start proton-pass-ssh-agent.service
SSH_AUTH_SOCK="$XDG_RUNTIME_DIR/proton-pass-ssh-agent/agent.sock" ssh-add -l
ssh -T git@github.com
```

GitHub returns status 1 after successful `ssh -T` authentication because it does
not provide shell access. Its greeting must identify the expected account.
Use `git ls-remote` against a repository you can access to verify Git access.

## Git commit signing

Register the selected public key as a signing key in your GitHub account.
GitHub authentication and signing registrations are separate.
Use the same public-key selector for Git signing:

```bash
git config --global gpg.format ssh
git config --global user.signingkey "$HOME/.ssh/github.pub"
git config --global gpg.ssh.program "$HOME/.local/libexec/proton-pass-ssh-agent/proton-pass-sign.sh"
git config --global commit.gpgsign true
```

Replace the example public-key filename with your selected identity.
The installed signing wrapper explicitly selects the Proton socket. This also
works when a terminal still has another `SSH_AUTH_SOCK` value. It does not
start another agent or fall back to local private keys.

For local signature verification, create an SSH allowed-signers file with the
Git author email followed by the public key. Set `gpg.ssh.allowedSignersFile`
to that file. Preserve entries needed to verify historical signatures.
The installer does not change Git signing settings or the allowed-signers file.
Before removing the integration, update Git's signing program and key settings.

## Startup and recovery

The service starts with the graphical session, not the lingering user manager
at boot. It waits up to two minutes for the default desktop keyring to unlock.
If the keyring remains unavailable, it fails without invoking Proton Pass.
Unlock the keyring, then restart the service. Other agent failures have bounded
systemd retries. Authentication errors remain visible in the journal.

```bash
systemctl --user status proton-pass-ssh-agent.service
journalctl --user -u proton-pass-ssh-agent.service -n 30 --no-pager
systemctl --user reset-failed proton-pass-ssh-agent.service
systemctl --user restart proton-pass-ssh-agent.service
```

If Proton requires authentication, source the installed shell hook and run
`pass-cli login` before restarting. If Proton reports a missing local encryption
key and confirms a successful forced logout, log in again with the D-Bus backend.
This clears an unusable local login session; it does not delete vault items.
Stop and investigate other errors instead of repeatedly deleting local state.
Do not switch to filesystem key storage.
Verify authentication again after a reboot and graphical login. A successful
check in the current session does not prove reboot persistence.

The lock check prevents startup while the keyring is locked. It cannot prevent
keyring deletion, session revocation, or a lock change after the check. Locking
the screen does not necessarily stop an already running SSH agent.

## Remove

Stop and disable the integration:

```bash
systemctl --user disable --now proton-pass-ssh-agent.service
```

Remove the include from `~/.ssh/config` and the installed hook lines from
`.profile` and `.bashrc`. Remove `~/.ssh/proton-pass-github.conf`,
`~/.local/libexec/proton-pass-ssh-agent/`, and the unit under
`${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/`. Then run
`systemctl --user daemon-reload` and open a new desktop session.
This does not remove vault items, local private keys, or Proton login data.
The maintenance uninstaller manages only the maintenance integration.

## Sources

- [Proton CLI storage configuration](https://protonpass.github.io/pass-cli/get-started/configuration/)
- [Proton CLI SSH agent](https://protonpass.github.io/pass-cli/commands/ssh-agent/)

The agent uses the installed CLI's refresh interval. Check
`pass-cli ssh-agent start --help` for its current default.
