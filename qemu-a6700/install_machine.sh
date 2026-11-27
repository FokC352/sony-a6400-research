#!/bin/sh
# Copy the sony-a6700 machine into a QEMU 10.0.0 source tree and register it (idempotent).
# usage: install_machine.sh /path/to/qemu-10.0.0
set -e
SRC="$1"; HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$SRC/hw/arm/virt.c" ] || { echo "not a QEMU tree: $SRC" >&2; exit 2; }
cp "$HERE/hw/arm/sony-a6700.c" "$SRC/hw/arm/sony-a6700.c"
grep -q "config SONY_A6700" "$SRC/hw/arm/Kconfig" || cat >> "$SRC/hw/arm/Kconfig" <<'KCONFIG'

config SONY_A6700
    bool
    default y
    depends on TCG && AARCH64
    select ARM_GIC
    select PL011
KCONFIG
grep -q "sony-a6700.c" "$SRC/hw/arm/meson.build" || \
  sed -i "/arm_ss.add(when: 'CONFIG_ARM_VIRT'/a arm_ss.add(when: ['CONFIG_SONY_A6700', 'TARGET_AARCH64'], if_true: files('sony-a6700.c'))" "$SRC/hw/arm/meson.build"
echo installed
