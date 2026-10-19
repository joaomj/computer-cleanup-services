# Development

The project requires Python 3.10 or later. Runtime code uses the Python
standard library. The project is not an installable Python package.
The shell installer copies the code into the selected user service directory.

## Local Checks

Check Python syntax without running maintenance:

```bash
python3 -m py_compile src/*.py
```

Check installer syntax:

```bash
for file in scripts/install scripts/uninstall scripts/install-proton-pass \
    shell/proton-pass-init.sh shell/proton-pass-sign.sh; do
    bash -n "$file" || exit
done
```

Run the existing test suite when required:

```bash
./scripts/verify
```

Tests cover configuration, cleanup, updates, security scanning, status
reporting, installation, and shell startup. Security tests parse synthetic
lockfiles and OVAL XML and use a fake Homebrew command; they do not use the
network. End-to-end tests create a temporary home, run the command line, and
answer the review prompt through a pseudo-terminal. They delete only
files inside that temporary home. Use an age threshold of `0` in tests to make
fresh files eligible, and a disk threshold of `0` to act as a machine under
pressure.

Do not run installers as development checks: they change the host. The
installation test runs the installer with intercepted service managers and a
temporary home.
The optional SSH integration also requires a live check with an unlocked desktop
keyring and an authenticated Proton account. Verify authentication and signing
after a fresh graphical login before claiming reboot persistence.

## Repository Data

Keep machine configuration, credentials, database backups, logs, and status
records outside this repository. Use generic paths in documentation.
Keep signing keys and personal Git settings in your local Git configuration.
