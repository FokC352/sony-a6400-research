#!/usr/bin/env bash
# BIONZ X Film Sim Studio (ILCE-6100 / ILCE-6400) for macOS/Linux (counterpart of run_film_sim_studio.bat).
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/_common.sh"

PY=$(find_python venv) || die "Python 3 was not found; create venv/ or install python3."
exec "$PY" "$REPO_ROOT/tools/film_sim_studio.py" "$@"
