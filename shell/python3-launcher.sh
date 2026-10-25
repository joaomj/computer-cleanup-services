#!/bin/sh
# Run Python from a supported interpreter.
#
# The installer records the interpreter it validated in python3-interpreter
# beside this file. Homebrew deletes that path when it upgrades or relinks the
# formula, so the recorded path is only the first candidate.
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
self="$here/$(basename -- "$0")"
recorded=$(cat "$here/python3-interpreter" 2>/dev/null || true)
on_path=$(command -v python3 2>/dev/null || true)
for candidate in "$recorded" \
    "$on_path" \
    "${HOMEBREW_PREFIX:+$HOMEBREW_PREFIX/bin/python3}" \
    "$HOME"/homebrew/opt/python@*/bin/python3 \
    /opt/homebrew/opt/python@*/bin/python3 \
    /usr/local/opt/python@*/bin/python3 \
    /home/linuxbrew/.linuxbrew/opt/python@*/bin/python3 \
    "$HOME"/homebrew/bin/python3 \
    /opt/homebrew/bin/python3 \
    /home/linuxbrew/.linuxbrew/bin/python3 \
    /usr/local/bin/python3 \
    /usr/bin/python3; do
    [ -x "$candidate" ] || continue
    [ "$candidate" = "$self" ] && continue
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        exec "$candidate" "$@"
    fi
done
printf '%s: no Python 3.10 or later found; rerun its installer\n' "$(basename -- "$here")" >&2
exit 1
