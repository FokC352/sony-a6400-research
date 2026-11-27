# ILCE-6700 (BIONZ XR, CXD90057) QEMU machine - first boot

Status: **the stock 2.00 Linux 4.9.337 kernel boots on a new `sony-a6700` QEMU machine, mounts
its initrd as root and runs the real `/sbin/init`, which loads the first kernel modules; the
boot then stops at the first driver that needs hardware that is not modelled (`dma330`).**
No UI, USB or TCP debugger exists yet. Inputs are camera-derived and stay under the
git-ignored `artifacts/`; this page lists identities and facts only. Evidence: E9202.

The ILCE-6700 is an AArch64 SoC (`CXD90057`, four Cortex-A35), so it needs a new machine
rather than a `TargetProfile` entry of the BIONZ X (`CXD90045`, ARM32) emulators.

## Inputs

| Input | Source | SHA-256 |
| --- | --- | --- |
| `vmlinux.bin` (AArch64 `Image`, 7,008,256 bytes, text offset `0x80000`) | `nflasha3` of the 2.00 update | `bf878384f09ac2fc7ca5cd673e7ededb803a214f3fb410e19102ce575d6b16da` |
| `initrd.img` (raw ext2, 5,218,304 bytes) | `nflasha3` of the 2.00 update | `76e56da1585c60b3addede9e6180f8695a8aca7ab4077d3253300472df9a5a19` |
| `cxd90057.dtb` (16,875 bytes) | `/setting` (`nflasha2`) read from the camera, E9201 | `c5735ad9c6da8c48fb24eeee0bb743d07c486a47d5db19bef13b5eaea24ad280` |
| `kemco.txt` (kernel parameters) | `/setting` read from the camera | not recorded here |

The device tree exists only on the camera (not in the update).

## The machine (`hw/arm/sony-a6700.c`, QEMU 10.0.0)

Everything comes from the camera's device tree: DRAM at `0x4_0000_0000`, GIC-400 (GICv2,
480 interrupts) at `0xF13A1000`, five `arm,pl011` UARTs from `0xF1000000` (SPI `0xDA`..),
generic timer at 2 MHz. The SoC windows `0xF0000000-0xF1FFFFFF` and `0xFE000000-0xFEFFFFFF`
are unimplemented devices that read as zero and log every access (`-d unimp`).

Boot contract observed from the image (offline and by running it):

- The `Image` starts at **EL3** (its first branch programs `VBAR_EL3`, `SCR_EL3`, `CPTR_EL3`,
  `ACTLR_EL3`, `CNTFRQ_EL0`, `HCR_EL2`, then `ERET`s to EL1). QEMU's `arm_load_kernel()`
  refuses AArch64 at EL3, so the machine loads the files itself and starts the CPU at the
  reset vector with a tiny trampoline that jumps to the Image.
- The image's own EL3 code passes the kernel the device tree at **`RAM_BASE + 0x2000`**
  (`0x4_0000_2000`); a DTB anywhere else is rejected ("invalid device tree blob").
- The kernel looks for a command-line override at `RAM_BASE + 0x1000` ("invalid cmdline
  override @ 0x400001000. Using default cmdline." when absent).
- An early `mrs/msr S3_1_C15_C2_1` touches Cortex-A35 `CPUECTLR_EL1`.

Licensing: `sony-a6700.c` is written against the QEMU 10.0.0 API and is released under
GPL-2.0-or-later like QEMU itself (SPDX header in the file); it is not part of the MIT-licensed
Python tooling. No QEMU source is copied into this repository.

## Build and run

```sh
sh qemu-a6700/build_qemu.sh /path/to/qemu-10.0.0        # tarball SHA-256 22c07560...3896
sh qemu-a6700/run_first_boot.sh /path/to/qemu-10.0.0/build /path/to/kernel-dir 60
```

## Result of the first boot (60 s, one CPU)

Reached, in order: AArch64 EL3 to EL1 hand-off, device-tree parse, GIC probe, the CXD clock
driver, the architected timer at 2 MHz, five PL011 UARTs, initrd unpack and the ext2 root
mount (`VFS: Mounted root (ext2 filesystem) on device 1:0`), then the real `/sbin/init`
loading `devno.ko` and `dma330.ko`. `dma330_probe` dereferences NULL (the PL330 DMA
controller is not modelled), and Sony's exception monitor takes over (`Exception>`).

Seen on the way and not yet explained:

- `GIC CPU mask not found - kernel will fail to boot.`
- CPU 1-3 fail to come online (spin-table with no secondary entry model); use `maxcpus=1`.
- `check_cr_sram_init_done timed out to wait for SRAM_INIT_DONE`.
- The most frequent unimplemented accesses are in `0xF1388xxx`, `0xF102Exxx` and `0xF108xxx`.

## Next steps

1. Model PL330 (`dma330`) at its device-tree address, the SRAM-init status register, and the
   SCU/clock/reset syscon registers the logs show, one contract at a time.
2. Spin-table secondary start and the GIC CPU-mask property.
3. Drivers after `dma330`: USB (`snps,dwc3`), PCIe end points, Arasan SDHCI.
4. A USB or TCP debugger only after the guest reaches the camera application: the repository's
   native debugger (`tools/native_camera/a6400_usb_debugd.c`) is ARM32 and written for the a6400.

## Clean Setting image

`make_clean_setting.sh` builds a NEW FAT image from a captured `nflasha2.bin` (mounted
read-only) that keeps the device tree, `kemco.txt` and the generic model/config files and
omits the settings backup (owner name and serial number), the face-registration data and the
service token. The old image's free-space remnants (DHCP leases, MAC addresses) are not
copied. A scan of the result finds no name, serial, MAC, lease, private IP or e-mail pattern.
This is a data-hygiene step for emulator inputs; it is not a factory reset of the camera.
