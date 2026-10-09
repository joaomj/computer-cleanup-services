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
bash -n scripts/install scripts/uninstall
```

Run the existing test suite when required:

```bash
./scripts/verify
```

Tests exercise configuration, cleanup, updates, and status reporting.
Do not run the installer as a development check: it changes the host.

## Repository Data

Keep machine configuration, credentials, database backups, logs, and status
records outside this repository. Use generic paths in documentation.
Keep signing keys and personal Git settings in your local Git configuration.
