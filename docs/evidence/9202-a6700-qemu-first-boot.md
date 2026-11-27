# E9202 - ILCE-6700 kernel first boot on a new `sony-a6700` QEMU machine

- **Date:** 2026-11-27.
- **Scope:** offline only. No camera, USB, SD or flash access; the inputs are the 2.00 update
  images and the device tree captured in E9201. No firmware bytes are published.
- **Machine:** `qemu-a6700/hw/arm/sony-a6700.c` (QEMU 10.0.0, tarball SHA-256
  `22c075601fdcf8c7b2671a839ebdcef1d4f2973eb6735254fd2e1bd0f30b3896`), scripts and notes in
  [qemu-a6700/README.md](../../qemu-a6700/README.md). Tests: none beyond the run below.

## 1. Inputs

| Input | Bytes | SHA-256 |
| --- | ---: | --- |
| `vmlinux.bin` (AArch64 `Image`, `nflasha3` of the 2.00 update) | 7,008,256 | `bf878384f09ac2fc7ca5cd673e7ededb803a214f3fb410e19102ce575d6b16da` |
| `initrd.img` (raw ext2, same partition) | 5,218,304 | `76e56da1585c60b3addede9e6180f8695a8aca7ab4077d3253300472df9a5a19` |
| `cxd90057.dtb` (`/setting`, read from the camera in E9201) | 16,875 | `c5735ad9c6da8c48fb24eeee0bb743d07c486a47d5db19bef13b5eaea24ad280` |

Kernel banner: `Linux version 4.9.337-scel+ ... (gcc 6.4.0, Sony CE Linux 14.0.1.0) #1 SMP
PREEMPT Sat May 24 03:39:03 JST 2025`.

## 2. Observations from the image and the device tree

- The `Image` header is valid (`ARM\x64`, text offset `0x80000`), but its first instruction
  branches to code that programs `VBAR_EL3`, `SCR_EL3`, `CPTR_EL3`, `ACTLR_EL3`, `CNTFRQ_EL0`
  and `HCR_EL2` and `ERET`s to EL1: it expects to start at EL3.
- The device tree declares four `arm,cortex-a35` CPUs with `spin-table`, an `arm,gic-400`
  at `0xF13A1000`, a 2 MHz generic timer, five `arm,pl011` UARTs from `0xF1000000`, and the
  SoC blocks (SCU, clocks, resets, PL330 DMA, DWC3 USB, PCIe, SDHCI).

## 3. Run

Stock `qemu-system-aarch64` with `-M virt` ran silently and stopped with a data abort at
`FAR 0xFE005010`, because the early code reads a SoC register that `virt` lacks and the
image starts at EL3. On the new machine the kernel starts through EL3 and reaches, in order:
EL3 to EL1 hand-off, device-tree parse, GIC probe, `CXD clock driver init done`, the
architected timer at 2 MHz, five PL011 UARTs (`ttyAMA0`..`ttyAMA4`, IRQ 250..254), initrd
unpack, `RAMDISK: ext2 filesystem found`, `VFS: Mounted root (ext2 filesystem) on device
1:0`, and the real `/sbin/init`, which loads `devno.ko` and `dma330.ko`. `dma330_probe`
dereferences NULL and Sony's exception monitor takes over (`Exception>`). About 22 KB of UART
output in the first 8 s.

Boot-contract facts learned by running it: the image's EL3 code hands the kernel the device
tree at `RAM_BASE + 0x2000`; a DTB elsewhere is rejected ("invalid device tree blob"); a
command-line override is looked up at `RAM_BASE + 0x1000`.

## 4. Boundaries

- Only the UART, GIC, timer and RAM are modelled. Every other SoC register block is an
  unimplemented device that reads zero. `GIC CPU mask not found`, `CPU1..3 failed to come
  online` and `check_cr_sram_init_done timed out` are unexplained or expected consequences.
- This is not a verified model of the SoC and not a camera application boot: no UI, no
  USB gadget and no TCP or USB debugger exist for this body yet.
- The kernel, initrd and device tree are proprietary and stay under `artifacts/`.

## 5. Next boundary

Model PL330 and the SRAM-init status register, then the SCU/clock/reset registers that the
`-d unimp` log shows (most frequent: `0xF1388xxx`, `0xF102Exxx`, `0xF108xxx`), then secondary
CPUs and the drivers after `dma330` (USB, PCIe, SDHCI).
