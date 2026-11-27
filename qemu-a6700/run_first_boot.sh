#!/bin/sh
# Boot the ILCE-6700 kernel and initrd on the sony-a6700 research machine.
# usage: run_first_boot.sh <qemu-build-dir> <kernel-dir> [seconds]
#   <kernel-dir> holds vmlinux.bin, initrd.img (from nflasha3 of the 2.00 update) and
#   cxd90057.dtb (from /setting). UART0 goes to uart0.log, unimplemented-device accesses to
#   unimp.log, both in the current directory.
set -e
B="$1"; K="$2"; T="${3:-60}"
[ -x "$B/qemu-system-aarch64" ] && [ -f "$K/vmlinux.bin" ] || { echo "usage: $0 qemu-build kernel-dir [seconds]" >&2; exit 2; }
timeout "$T" "$B/qemu-system-aarch64" -M sony-a6700 -m 512M -display none -no-reboot \
  -serial file:uart0.log -monitor none \
  -kernel "$K/vmlinux.bin" -initrd "$K/initrd.img" -dtb "$K/cxd90057.dtb" \
  -append "root=/dev/ram0 rw init=/sbin/init ramdisk_size=8192 console=ttyAMA0,460800 earlycon=pl011,0xf1000000 loglevel=8 maxcpus=1" \
  -d unimp,guest_errors -D unimp.log || true
echo "UART bytes: $(wc -c < uart0.log)"
