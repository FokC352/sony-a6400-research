#!/usr/bin/env bash
# Smoke-test the portable BIONZ Look Lab binary from a folder that contains only itself.
#
# macOS/Linux counterpart of smoke_look_lab_portable.ps1: copies
# dist/BIONZ-Look-Lab-<os>-<arch>/bionz-look-lab into an empty folder, starts it there and checks that
# the process is still alive after a few seconds. A missing file or an immediate crash shows up as a
# process that is gone before the timeout. Unlike the Windows script it does not count main windows.
#
# Usage: scripts/smoke_look_lab_portable.sh [--seconds N] [--root EMPTY_DIR]
#   --root defaults to a fresh temporary folder; an existing non-empty folder is refused.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/_common.sh"

SECONDS_ALIVE=8
ROOT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --seconds) SECONDS_ALIVE="${2:?--seconds needs a value}"; shift 2 ;;
        --root) ROOT="${2:?--root needs a value}"; shift 2 ;;
        -h | --help) sed -n '2,10p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
case "$SECONDS_ALIVE" in '' | *[!0-9]*) die "--seconds must be a whole number" ;; esac

OS=$(host_os)
SOURCE="$REPO_ROOT/dist/BIONZ-Look-Lab-$OS-$(host_arch)/bionz-look-lab"
[ -x "$SOURCE" ] || die "$SOURCE is missing; run scripts/build_look_lab_release.sh first"
if pgrep -x bionz-look-lab >/dev/null 2>&1; then
    die "a bionz-look-lab process is already running; close it first"
fi
if [ "$OS" = linux ] && [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    die "no DISPLAY or WAYLAND_DISPLAY; the GUI smoke test needs a graphical session"
fi

if [ -z "$ROOT" ]; then
    ROOT=$(mktemp -d "${TMPDIR:-/tmp}/LookLabTest.XXXXXX")
elif [ -e "$ROOT" ] && [ -n "$(ls -A "$ROOT")" ]; then
    die "$ROOT is not empty; pass an empty or new folder"
else
    mkdir -p "$ROOT"
fi

PID=""
cleanup() {
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null || true
        wait "$PID" 2>/dev/null || true
    fi
    rm -rf "$ROOT"
}
trap cleanup EXIT

cp "$SOURCE" "$ROOT/bionz-look-lab"
FILES=$(ls -A "$ROOT")
[ "$FILES" = "bionz-look-lab" ] || die "the smoke folder must contain only the binary, found: $FILES"

(cd "$ROOT" && exec ./bionz-look-lab </dev/null >/dev/null 2>&1) &
PID=$!
sleep "$SECONDS_ALIVE"

if ! kill -0 "$PID" 2>/dev/null; then
    wait "$PID" 2>/dev/null && CODE=0 || CODE=$?
    die "bionz-look-lab exited (code $CODE) before ${SECONDS_ALIVE}s; the portable build failed to start from an empty folder"
fi
echo "PASS: bionz-look-lab ran for ${SECONDS_ALIVE}s from a folder containing only the binary (pid $PID)."
