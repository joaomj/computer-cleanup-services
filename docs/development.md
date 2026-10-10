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

Tests exercise configuration, cleanup, updates, and status reporting.
Do not run installers as development checks: they change the host.
The optional SSH integration also requires a live check with an unlocked desktop
keyring and an authenticated Proton account. Verify authentication and signing
after a fresh graphical login before claiming reboot persistence.

## Repository Data

Keep machine configuration, credentials, database backups, logs, and status
records outside this repository. Use generic paths in documentation.
Keep signing keys and personal Git settings in your local Git configuration.
