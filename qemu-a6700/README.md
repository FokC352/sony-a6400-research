# ILCE-6700 (BIONZ XR, CXD90057) QEMU machine - first boot

Status: **the stock 2.00 Linux 4.9.337 kernel boots on a new `sony-a6700` QEMU machine with the
camera's own boot parameters, mounts its initrd as root, runs the real `/sbin/init` and loads
`devno`, `dma330` and `crypt-emmc`; the boot stops at the eMMC crypto engine, which is not modelled.**
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

## Sony GPL kernel source (hardware map)

Sony publishes the GPL source for this body: `linux-kernel.tar.gz` for ILCE-6700 Ver.2.00 at
<https://oss.sony.net/Products/Linux/DI/ILCE-6700_Ver200.html> (109,976,756 bytes, SHA-256
`fb39a21e67d6728e716adce36505f2b38ef8b37a76e3560025059371f373ec90`, downloaded under Sony's
notice, kept under `artifacts/sony-gpl/`, not in this repository). Its UDIF platform layer
(`drivers/udif/mach-cxd900xx`) gives the SoC map used by the machine: DDRC at `0xF105A000 +
0x1000*n` (INTSTAT is `+0x44C`), SCU at `0xF1388000` (16-byte DATA/SET/CLR groups), eSRAM at
`0xFE000000` (16 MiB), DMA330 interrupt bases, and the `memrsv=...:uc` non-cached windows.

## Boot contract (from the updater partition and `kemco.txt`)

`/boot/loadaddr.txt` on `nflasha1` reads `0x400080000,0x408000000,0x400001000,0x400002000`: kernel,
initrd, command-line blob (`kemco.txt`), device tree. `kemco.txt` carries the camera's real
parameters (`mem=4G`, `initrd=0x408000000,0x1000000`, `memrsv=...` incl. `:uc` windows,
`adrs_conv.*`, `console=ttynull0`). Without them the DMA module fails with `arch_phys_to_noncache`
errors; with them it loads.

## Build and run

```sh
sh qemu-a6700/build_qemu.sh /path/to/qemu-10.0.0        # tarball SHA-256 22c07560...3896
sh qemu-a6700/run_first_boot.sh /path/to/qemu-10.0.0/build /path/to/kernel-dir 90
```

## Result (90 s, one CPU)

Reached, in order: EL3 to EL1 hand-off, device-tree parse, GIC probe, the CXD clock driver, the
2 MHz timer, five PL011 UARTs, USB PHY `SRAM_INIT_DONE`/`SRAM_EXT_LD_DONE`, initrd ext2 root, the
real `/sbin/init`, then `devno.ko`, **`dma330.ko` (both controllers prepared)** and `crypt-emmc.ko`.
`cryptEmmc_reset` calls `cryptEmmc_setKeyCovertly`, which jumps to a bad address inside the module
(the eMMC crypto engine is not modelled), and Sony's exception monitor takes over.

Not yet explained: `GIC CPU mask not found`; CPU 1-3 fail to come online (use `maxcpus=1`).

## Next steps

1. The eMMC crypto engine (`crypt-emmc`) and the Arasan SDHCI/eMMC controller so that the `nflasha*`
   block devices appear (`emmc.ko` is the next module in `insmod_drv1`).
2. Secondary CPUs (spin-table) and the GIC CPU mask.
3. The remaining modules of `insmod_drv2` (USB, PCIe, display, sensor, ...), then the camera
   application. A USB or TCP debugger only after that: the repository's native debugger
   (`tools/native_camera/a6400_usb_debugd.c`) is ARM32 and written for the a6400.

## Scrubbed copies of the captured partitions

`scrub_dump.sh <captures-dir> <output-dir>` makes privacy-scrubbed COPIES for the owner's local
emulator use (the captured images are only mounted read-only). Each FAT partition is rebuilt
as a new image that keeps the generic files and none of the free-space remnants (old DHCP
leases, MAC addresses); the settings backup (owner name, serial number), the face-registration
data, the service token, the client certificate and private key, the network and access logs
and the updater history are left out or zero-filled, and the unreadable MPR backup is
zero-filled. A scan of the results finds no owner name, serial number, MAC address, lease,
private IP, private key or personal e-mail pattern. This is data hygiene, not a factory reset
of the camera and not a licence to redistribute Sony's firmware.
