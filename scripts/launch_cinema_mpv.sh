#!/usr/bin/env bash
# Sony a6400 Cinema Monitor (mpv + AMD FSR upscaler) on macOS or Linux.
# Counterpart of launch_cinema_mpv.bat; artifacts/ stays at the repository root.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/_common.sh"

STREAM="http://127.0.0.1:8080/liveview.mjpg"
echo "========================================================"
echo "  Sony a6400 Cinema Monitor (mpv + AMD FSR Upscaler)"
echo "========================================================"
echo "Connecting to Live View stream at $STREAM"
echo "Using AMD FidelityFX Super Resolution (FSR) GLSL shader..."
echo

status=127
if command -v mpv >/dev/null 2>&1; then
    mpv "$STREAM" --profile=low-latency --untimed \
        --title="Sony a6400 Cinema Monitor [AMD FSR]" \
        --glsl-shaders="$REPO_ROOT/artifacts/shaders/FSR.glsl" \
        --geometry=1920x1080 --autofit=100% "$@"
    status=$?
fi

if [ "$status" -ne 0 ]; then
    echo
    echo "Note: If mpv is not installed or not in PATH (brew install mpv / apt install mpv), you can open:"
    echo "  http://127.0.0.1:8080/"
    echo "in your web browser (Chrome/Edge/Firefox) with the built-in WebGL2 AMD FSR engine!"
fi
exit "$status"
