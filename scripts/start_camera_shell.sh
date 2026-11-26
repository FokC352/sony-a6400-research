#!/usr/bin/env bash
# Sony ILCE-6400 live Wi-Fi debug terminal (port 2323) for macOS/Linux.
# Counterpart of start_camera_shell.bat, which runs the tool with `py -3.12`.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/_common.sh"

echo "========================================================"
echo "  SONY ILCE-6400 LIVE WI-FI ROOT TERMINAL (Port 2323)"
echo "========================================================"

PY=$(command -v python3.12 || true)
if [ -z "$PY" ]; then
    PY=$(command -v python3) || die "Python 3.12+ was not found on PATH."
    "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 12))' ||
        die "$PY is $("$PY" -V 2>&1); Python 3.12+ is required."
fi
exec "$PY" "$REPO_ROOT/tools/cam_live_terminal.py" "$@"
