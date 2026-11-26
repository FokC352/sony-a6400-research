#!/usr/bin/env bash
# Standalone macOS/Linux packager for Sony BIONZ X Mod Manager.
#
# macOS/Linux counterpart of build_mod_manager_exe.ps1: provisions an isolated .venv-packager
# environment, caches Sony-PMCA-RE, bundles libusb-1.0 + shipped .mod binaries (plus optional MLv2
# and debugger binaries), and builds dist/BionzX-Mod-Manager/ via PyInstaller.
#
# Usage: scripts/build_mod_manager_exe.sh [--onefile] [--mlv2-dir DIR] [--include-debugger-binaries]
#                                        [--skip-pmca-download] [--skip-release-download]
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/_common.sh"

ONEFILE=0
MLV2_DIR=""
INCLUDE_DBG=0
SKIP_PMCA=0
SKIP_RELEASE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --onefile) ONEFILE=1; shift ;;
        --mlv2-dir) MLV2_DIR="${2:?--mlv2-dir needs a value}"; shift 2 ;;
        --include-debugger-binaries) INCLUDE_DBG=1; shift ;;
        --skip-pmca-download) SKIP_PMCA=1; shift ;;
        --skip-release-download) SKIP_RELEASE=1; shift ;;
        -h | --help) sed -n '2,9p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

OS=$(host_os)
cd "$REPO_ROOT"

PACKAGER_PY="$REPO_ROOT/.venv-packager/bin/python"
if [ ! -x "$PACKAGER_PY" ]; then
    BASE_PY=$(find_python .venv venv) || die "Python 3.11+ was not found. Install Python and retry."
    echo "[1/4] Creating isolated .venv-packager environment..."
    "$BASE_PY" -m venv "$REPO_ROOT/.venv-packager"
fi

echo "[2/4] Installing GUI & PyInstaller dependencies..."
"$PACKAGER_PY" -m pip install --upgrade pip --quiet
"$PACKAGER_PY" -m pip install -r "$REPO_ROOT/requirements-gui.txt" --quiet
if [ "$OS" = linux ]; then
    # pywebview has no bundled GUI backend on Linux; the Qt extra makes the bundle self-contained.
    "$PACKAGER_PY" -m pip install "pywebview[qt]>=5.3,<7" --quiet
fi

echo "[3/4] Preparing bundled Sony-PMCA-RE, official MLv2, and Arrivederci v5..."
if [ "$SKIP_PMCA" = 1 ]; then
    PMCA_PATH=$("$PACKAGER_PY" -c "from src.a6400.mod_manager.tools_bundle import find_pmca_dir; p = find_pmca_dir(); print(p or '')")
else
    PMCA_PATH=$("$PACKAGER_PY" -c "from src.a6400.mod_manager.tools_bundle import ensure_pmca; print(ensure_pmca())")
fi

if [ -n "$MLV2_DIR" ]; then
    "$PACKAGER_PY" -c "import sys; from pathlib import Path; from src.a6400.mod_manager.tools_bundle import validate_mlv2_build; print(validate_mlv2_build(Path(sys.argv[1]))['path'])" "$MLV2_DIR"
elif [ "$SKIP_RELEASE" = 0 ]; then
    MLV2_DIR=$("$PACKAGER_PY" -c "from src.a6400.mod_manager.tools_bundle import ensure_mlv2_release; print(ensure_mlv2_release()['path'])")
fi

if [ "$SKIP_RELEASE" = 0 ]; then
    ARRIVEDERCI_PATH=$("$PACKAGER_PY" -c "from src.a6400.mod_manager.tools_bundle import ensure_arrivederci_v5; print(ensure_arrivederci_v5())")
else
    ARRIVEDERCI_PATH=$("$PACKAGER_PY" -c "from src.a6400.mod_manager.tools_bundle import find_arrivederci_v5; p = find_arrivederci_v5(); print(p or '')")
fi

export BIONZX_REPO_ROOT="$REPO_ROOT"
export BIONZX_PMCA_DIR="$PMCA_PATH"
export BIONZX_MLV2_DIR="$MLV2_DIR"
export BIONZX_ARRIVEDERCI_PATH="$ARRIVEDERCI_PATH"
export BIONZX_INCLUDE_DEBUGGER_BIN="$INCLUDE_DBG"
export BIONZX_ONEFILE="$ONEFILE"

echo "[4/4] Building standalone executable via PyInstaller..."
"$PACKAGER_PY" -m PyInstaller --noconfirm --clean \
    --workpath "$REPO_ROOT/build/mod-manager" \
    --distpath "$REPO_ROOT/dist" \
    "$REPO_ROOT/tools/mod_manager.spec" ||
    die "PyInstaller failed (close any running BionzX-Mod-Manager and retry)."

echo
if [ "$ONEFILE" = 1 ]; then
    echo "Done! Standalone executable: $REPO_ROOT/dist/BionzX-Mod-Manager"
else
    echo "Done! Standalone bundle: $REPO_ROOT/dist/BionzX-Mod-Manager/BionzX-Mod-Manager"
fi
