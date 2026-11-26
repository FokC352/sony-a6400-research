#!/usr/bin/env bash
# Launch the BIONZ X Mod Manager GUI on macOS or Linux (counterpart of launch_mod_manager.bat).
# Provisions an isolated .venv-gui environment on first run when the GUI dependencies are missing.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/_common.sh"

OS=$(host_os)
cd "$REPO_ROOT"

GUI_PY="$REPO_ROOT/.venv-gui/bin/python"
BASE_PY=$(find_python .venv venv) || die "Python 3 was not found on PATH. Install Python 3.11+ and re-run."

PY="$BASE_PY"
[ -x "$GUI_PY" ] && PY="$GUI_PY"

IMPORTS="webview, usb, libusb_package, Cryptodome, elftools, yaml, asn1crypto, axmlparserpy, certifi, tlslite"
PACKAGES=("pywebview>=5.3,<7" pyusb==1.3.1 "libusb-package>=1.0.26" pycryptodomex==3.24.2 pyelftools==0.33
    PyYAML==6.0.3 "asn1crypto>=1.5.1" axmlparserpy==0.0.4 "certifi>=2024.8.30" "tlslite-ng>=0.7.6")
if [ "$OS" = linux ]; then
    # pywebview needs a GUI backend on Linux; an isolated venv cannot see the system GTK bindings.
    IMPORTS="$IMPORTS, qtpy"
    PACKAGES+=("pywebview[qt]>=5.3,<7")
fi

if ! "$PY" -c "import $IMPORTS" >/dev/null 2>&1; then
    if [ ! -x "$GUI_PY" ]; then
        echo "Creating isolated .venv-gui environment..."
        "$BASE_PY" -m venv "$REPO_ROOT/.venv-gui" || die "Failed to create .venv-gui environment."
    fi
    echo "Installing GUI dependencies into .venv-gui..."
    "$GUI_PY" -m pip install --upgrade pip >/dev/null 2>&1 || true
    "$GUI_PY" -m pip install "${PACKAGES[@]}" || die "Failed to install GUI dependencies."
    PY="$GUI_PY"
fi

exec "$PY" -m tools.mod_manager_gui "$@"
