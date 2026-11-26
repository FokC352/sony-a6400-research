#!/usr/bin/env bash
# CinemaHUD PTP launcher for macOS/Linux (counterpart of launch_cinemahud_ptp.bat).
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/_common.sh"

cd "$REPO_ROOT"
PY=$(find_python venv) || die "Python 3 was not found; create venv/ or install python3."
echo "CinemaHUD - original Sony firmware / USB PC Remote"
echo "Set the camera USB Connection to PC Remote before starting."
exec "$PY" -m tools.research.ptp_liveview_client --source ptp --serve --no-auto-deploy "$@"
