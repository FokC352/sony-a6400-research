#!/bin/sh
# Build a cleaned ILCE-6700 Setting (nflasha2) image for emulator use from a captured one.
# usage: make_clean_setting.sh <captured nflasha2.bin> <output.img>   (run as root in WSL/Linux)
# The captured image is only mounted read-only. The output is a NEW FAT image that carries
# the device tree, the kernel command-line file and the generic model/config files; it omits
# the settings backup (owner name, serial number), the face registration data and the service
# token, and it does not contain the old image's free-space remnants (DHCP leases, MACs).
set -e
IN="$1"; OUT="$2"
[ -f "$IN" ] && [ -n "$OUT" ] || { echo "usage: $0 captured.bin clean.img" >&2; exit 2; }
[ ! -e "$OUT" ] || { echo "refusing to overwrite $OUT" >&2; exit 2; }
SIZE=$(stat -c %s "$IN")
SRC=$(mktemp -d); DST=$(mktemp -d)
trap 'umount "$SRC" 2>/dev/null || true; umount "$DST" 2>/dev/null || true; rmdir "$SRC" "$DST" 2>/dev/null || true' EXIT
mount -o loop,ro "$IN" "$SRC"
truncate -s "$SIZE" "$OUT"
mkfs.fat -F 16 -n SETTING "$OUT" >/dev/null
mount -o loop "$OUT" "$DST"
for f in cxd90057.dtb kemco.txt DmmCfg.bin ulogio.bin defdet.bin psd.bin facedmy.bin development.txt; do
  [ -f "$SRC/$f" ] && cp "$SRC/$f" "$DST/$f"
done
: > "$DST/.hasp"; head -c 88 /dev/zero > "$DST/.hasp"
mkdir -p "$DST/updater"; : > "$DST/updater/comp"
umount "$DST"
echo "wrote $OUT ($SIZE bytes)"
