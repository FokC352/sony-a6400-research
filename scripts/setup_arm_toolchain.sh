#!/usr/bin/env bash
# Root-free arm-linux-gnueabi cross toolchain for the native module builds.
#
# The Mod Loader / SMOD builds need arm-linux-gnueabi-gcc/ld/objdump. Rather
# than requiring a system install, this downloads the Ubuntu cross packages and
# extracts them under a prefix, then writes wrapper scripts that supply the
# paths the extracted compiler cannot know (sysroot, gcc-cross dir, host libs).
#
# On macOS there are no Ubuntu packages to unpack. Instead the script wraps an installed
# arm-unknown-linux-gnueabi toolchain (soft-float ABI, the same ABI the builds pass with
# -mfloat-abi=softfp) under the arm-linux-gnueabi-* names the build scripts call:
#         brew tap messense/macos-cross-toolchains
#         brew install arm-unknown-linux-gnueabi
#
# Usage:  bash scripts/setup_arm_toolchain.sh [prefix]
#         export PATH="<prefix>/bin:$PATH"
#
# Linux needs network (apt-get download) and dpkg-deb; no root.
set -euo pipefail

PREFIX="${1:-artifacts/toolchain/arm-linux-gnueabi}"
mkdir -p "$PREFIX"
PREFIX="$(cd "$PREFIX" && pwd -P)"
DEBS="$PREFIX/debs"
ROOT="$PREFIX/root"
BIN="$PREFIX/bin"
TOOLS="gcc ld as objdump objcopy readelf ar ranlib nm size strip"

if [ -x "$BIN/arm-linux-gnueabi-gcc" ]; then
    echo "toolchain already present: $BIN" >&2
    echo "export PATH=\"$BIN:\$PATH\""
    exit 0
fi

probe_toolchain() {
    probe=$(mktemp -d)
    trap 'rm -rf "$probe"' EXIT
    printf 'typedef unsigned int u32;\nu32 f(u32 a, u32 b){return a+b;}\n' > "$probe/p.c"
    PATH="$BIN:$PATH" arm-linux-gnueabi-gcc -mthumb -nostdlib -O2 -c "$probe/p.c" -o "$probe/p.o"
    PATH="$BIN:$PATH" arm-linux-gnueabi-objdump -d "$probe/p.o" >/dev/null
    echo "toolchain ready: $PREFIX" >&2
    echo "export PATH=\"$BIN:\$PATH\""
}

if [ "$(uname -s)" = Darwin ]; then
    CROSS=""
    for dir in "$(brew --prefix 2>/dev/null || true)/bin" /opt/homebrew/bin /usr/local/bin $(echo "$PATH" | tr ':' ' '); do
        if [ -x "$dir/arm-unknown-linux-gnueabi-gcc" ]; then CROSS="$dir/arm-unknown-linux-gnueabi"; break; fi
    done
    if [ -z "$CROSS" ]; then
        echo "arm-unknown-linux-gnueabi-gcc was not found. Install it with:" >&2
        echo "    brew tap messense/macos-cross-toolchains && brew install arm-unknown-linux-gnueabi" >&2
        exit 1
    fi
    mkdir -p "$BIN"
    for tool in $TOOLS; do
        printf '#!/bin/sh\nexec "%s-%s" "$@"\n' "$CROSS" "$tool" > "$BIN/arm-linux-gnueabi-$tool"
        chmod +x "$BIN/arm-linux-gnueabi-$tool"
    done
    probe_toolchain
    exit 0
fi

command -v apt-get >/dev/null || { echo "apt-get is required" >&2; exit 1; }
command -v dpkg-deb >/dev/null || { echo "dpkg-deb is required" >&2; exit 1; }

mkdir -p "$DEBS" "$ROOT" "$BIN"
echo "resolving packages..." >&2
apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts \
    --no-breaks --no-replaces --no-enhances \
    gcc-arm-linux-gnueabi binutils-arm-linux-gnueabi \
    libc6-dev-armel-cross linux-libc-dev-armel-cross 2>/dev/null \
    | grep -E '^[a-z0-9][a-z0-9+.-]*$' | sort -u > "$PREFIX/packages.txt"
echo "downloading $(wc -l < "$PREFIX/packages.txt") packages..." >&2
mapfile -t packages < "$PREFIX/packages.txt"
( cd "$DEBS" && apt-get download "${packages[@]}" >/dev/null 2>&1 )
for deb in "$DEBS"/*.deb; do dpkg-deb -x "$deb" "$ROOT"; done

GCCDIR=$(echo "$ROOT"/usr/lib/gcc-cross/arm-linux-gnueabi/*/ | awk '{print $1}')
SYSROOT="$ROOT/usr/arm-linux-gnueabi"
[ -x "$ROOT/usr/bin/arm-linux-gnueabi-gcc" ] || { echo "gcc not found in the extracted tree" >&2; exit 1; }
[ -d "$GCCDIR" ] || { echo "gcc-cross directory not found" >&2; exit 1; }

for tool in $TOOLS; do
    extra=""
    [ "$tool" = gcc ] && extra="--sysroot=$SYSROOT -B$GCCDIR"
    cat > "$BIN/arm-linux-gnueabi-$tool" <<EOF
#!/bin/sh
export LD_LIBRARY_PATH="$ROOT/usr/lib/x86_64-linux-gnu:$ROOT/usr/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
exec "$ROOT/usr/bin/arm-linux-gnueabi-$tool" $extra "\$@"
EOF
    chmod +x "$BIN/arm-linux-gnueabi-$tool"
done

probe_toolchain
