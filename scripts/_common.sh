# shellcheck shell=bash
# Shared helpers for the macOS/Linux launchers in scripts/. Source it; do not run it.
# Bash 3.2 compatible (the /bin/bash shipped with macOS).

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

die() {
    echo "error: $*" >&2
    exit 1
}

# Prints "macos" or "linux"; any other host is refused.
host_os() {
    case "$(uname -s)" in
        Darwin) echo macos ;;
        Linux) echo linux ;;
        *) die "unsupported host $(uname -s); use the Windows .bat/.ps1 script instead" ;;
    esac
}

# Prints "arm64" or "x64" (or the raw machine name for anything else).
host_arch() {
    case "$(uname -m)" in
        arm64 | aarch64) echo arm64 ;;
        x86_64 | amd64) echo x64 ;;
        *) uname -m ;;
    esac
}

# find_python [venv-dir ...]: prints the first <repo>/<venv-dir>/bin/python that exists,
# falling back to python3 on PATH. Returns 1 when neither is available.
find_python() {
    local dir
    for dir in "$@"; do
        if [ -x "$REPO_ROOT/$dir/bin/python" ]; then
            echo "$REPO_ROOT/$dir/bin/python"
            return 0
        fi
    done
    command -v python3 || return 1
}

# sha256_of FILE: prints the lowercase hex digest only.
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

# file_size_mb FILE: size in MB with one decimal.
file_size_mb() {
    local bytes
    bytes=$(wc -c <"$1" | tr -d ' ')
    awk -v b="$bytes" 'BEGIN { printf "%.1f", b / 1048576 }'
}
