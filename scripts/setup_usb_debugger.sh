#!/usr/bin/env bash
# Run tools/setup_usb_debugger.py on macOS/Linux (counterpart of setup_usb_debugger.bat).
# Falls back to a local .venv-debugger with pycryptodome + pyelftools when they are missing.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/_common.sh"

cd "$REPO_ROOT"
PY=$(find_python venv) || die "Python 3 was not found on PATH. Install Python 3.12+ and re-run."

if ! "$PY" -c "import Cryptodome, elftools" >/dev/null 2>&1; then
    DEBUGGER_PY="$REPO_ROOT/.venv-debugger/bin/python"
    if [ ! -x "$DEBUGGER_PY" ]; then
        echo "Creating local .venv-debugger and installing pycryptodome + pyelftools..."
        "$PY" -m venv "$REPO_ROOT/.venv-debugger"
    fi
    PY="$DEBUGGER_PY"
    "$PY" -c "import Cryptodome, elftools" >/dev/null 2>&1 || "$PY" -m pip install --quiet pycryptodome pyelftools
fi

exec "$PY" "$REPO_ROOT/tools/setup_usb_debugger.py" "$@"
