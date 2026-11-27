# E0685 — Full Custom OS on a physical ILCE-6400 (MSC): handover, av-cam load, Wi-Fi-branch crash, loader-branch liveview

Date: 2026-11-22. Physical ILCE-6400 in MSC mode, stock 3.0.27_nl-rt106+. RAM-only tests authorized
by the user: the payload lives in the `/tmp_bt` tmpfs and in RAM page lists, no eMMC/SD/SD-flash
write, no service mode, no firmware install, no driver replacement. The `deadman=90` watchdog (or
the exception monitor) returns the camera to stock each time; every run below is followed by a
post-reboot state check.

## Question

Does the **full** Custom OS (custom kernel Image + `custom-full-initrd.ext2.gz`, not the hybrid
initramfs of E0684) run on the physical camera: boot, av-cam/LIRO, live view, and the Custom OS UI?

## Inputs and bundles

| Input | Identity |
| --- | --- |
| kernel | `artifacts/custom-kernel-ramboot-v1/custom-Image`, 4,421,888 B, `33803be11d…26b8` |
| full initrd | `custom-full-initrd.ext2.gz`; `b9392bb9…259b3` (first runs), `43f20544…da7f9` (OSD path selectable), `d8c527e5…71cd` (kmsg-instrumented `hybrid_gui`) |
| bundles | `artifacts/custom-kernel-fullos-camera-v1` (hardware branch), `artifacts/custom-kernel-fullos-loader-v1` (loader branch: `custom_os.qemu=1 [custom_os.osd=stream]`) |
| RAMBOOT.KO / manifest | `f01746a3…`, per-bundle FNV manifest verified by the launcher |

The launcher (`custom_os/launcher/launch_custom_kernel_flow.py`, run with `C:\Python314` for
`pycryptodome`) brings Wi-Fi up over USB, starts `tcp_debugd`, streams the payload over TCP, stages
it in the 12 MiB `/tmp_bt/stage`, runs the `RAMBOOT.KO` dryrun, then the live `deadman=90` insmod.

## Results

### 1. Hardware branch: the full OS boots, av-cam loads, then the Wi-Fi/SDIO path faults [C]

`artifacts/tests/camera-fullos-live-1` (initrd `b9392bb9…`, cmdline `c246f316…`, no
`custom_os.qemu` token). Dryrun passed; live TCP dropped at t=3.04 s. The ring shows:

```
[RAMBOOT] staged RBKERN.IMG 4421888 -> PA 0x00108000 (fnv 0xee8ba175)
[RAMBOOT] staged RBINITRD.CPIO 1073563 -> PA 0x00700000 (fnv 0x8c574aca)
[RAMBOOT] committing handover: trampoline 640 bytes at PA 0x02981000
RAMDISK: gzip image found at block 0
VFS: Mounted root (ext2 filesystem) on device 1:0.
eMMC: HS200 mode ... eMMC: 1864MiB, Samsung 4FTE4R
nflasha: nflasha1 .. nflasha23
LIRO (liro) rev:4-MUSASHI
Param:adrs=035c6000, debug=0, delay=0
Load firmware /system/av-cam.bin to 635c6000
0 -> 635c6000 : 0 10064e8 loaded (OK)
ram 644f1000 + 000d0798 / bss 63fa1734 + 0049d625
start entry_pre(635c6044) ... start entry_main(635c6058)
subsys_request_irq: IRQ150 is occupied by cxd900x0-uart.
Compat-wireless backport release ... cfg80211 ...
vector exception (0x000) at 0x00000000   (task irq/221-mmc1 pid 484, modules include mmcioWrapper)
WARMBOOT
```

So the custom kernel + full initrd boot on hardware and load av-cam with the correct physical
address (the same `adrs=035c6000` the camera uses), but the **hardware branch's Wi-Fi/SDIO
bring-up** faults before `imlaunch` and the exception monitor warm-reboots. This is the physical
counterpart of E0410, and it is why the QEMU Phase B gate selects the loader branch.

### 2. Loader branch (`--osd`): boot and live view, no UI [C]

`artifacts/tests/camera-fullos-loader-live-1` (initrd `b9392bb9…`, cmdline `4e4162e1…`,
`custom_os.qemu=1`). Dryrun passed; TCP dropped at t=3.07 s; the ring shows the same boot and
`LIRO … Param:adrs=035c6000 … entry_pre/entry_main`, then no exception — the OS ran until the
deadman rebooted it. On the panel the user saw **live view without any Custom OS UI**.

### 3. Loader branch (`--stream`): live view, still no Custom OS UI [C]

`artifacts/tests/camera-fullos-stream-live-1` (initrd `43f20544…`, cmdline `1270d2a1…`,
`custom_os.qemu=1 custom_os.osd=stream`). Dryrun passed; TCP dropped at t=3.08 s; the ring again
shows the boot + av-cam load and no exception, and the OS ran until the deadman. The user observed
live view with **focus-peaking colouring**, i.e. the panel/OSD composition is alive, but the Custom
OS UI (`hybrid_gui`) was still not visible. This run switched the OSD mapping from `/dev/mem`
(`--osd`) to `/dev/stream` (`--stream`), the camera's proven OSD path (E0074/E0298), so the OSD
device is not the whole story.

### 4. `hybrid_gui` diagnostics to `/dev/kmsg` [C]

The bridge path previously printed only to the console, which is not captured by the camera klog
ring. `hybrid_gui` now mirrors the bridge diagnostics with `kmsg()` (shm failure, OSD map failure,
`[HYBRID_VPH] bridge vph_kick=…`, `[HYBRID_GUI_READY] bridge shm=… osd=…`, `first_frame_us`).

The first instrumented run (`artifacts/tests/camera-fullos-stream-kmsg-1`, initrd `d8c527e5…`)
produced **no `[HYBRID_*]` lines at all** in its ring (CRC `0xc2bede4b`, 4 boots). The ring does show
the custom boot (`#5 SMP PREEMPT RT Sat Nov 21 23:39:00 CET 2026`), `VFS: Mounted root`, `LIRO …
Param:adrs=035c6000` and then the deadman `WARMBOOT`, with **no Wi-Fi/cfg80211 lines** in that boot —
so the loader branch was taken (the hardware branch's Wi-Fi does appear in the first boot's
section). Two explanations remain: `hybrid_gui` never ran, or its `/dev/kmsg` writes did not reach
the klog ring/device in this boot.

To separate them, `custom_start.sh` now writes shell-level markers (`[CUSTOM_OS_SH] branch=…`,
`osd_flag=…`, `imlaunch pid=…`, `hybrid_gui pid=…`) and `bridge_main` writes a `[HYBRID_SH]
bridge_main osd=… vph_kick=…` entry line. Build `artifacts/tests/custom-os-build-16`
(`hybrid_gui` `158a15cc…`), initrd `993c59c5…`; the run `artifacts/tests/camera-fullos-stream-kmsg-2`
carries it. Its klog ring, however, came back with only 11,297 bytes / `boot_count` 1 after the
deadman — the camera klog ring **does not retain the custom boot's records across the warm reboot**
in that run, so the kmsg path cannot be read afterwards. During that run the user saw live view, no
UI, and a flicker roughly every 3 s (the replay's three `IS o6 3200/2030` OSD re-opens are ~4 s
apart).

The OSD mapping itself was then corrected against the captured stock contract: E0298 records one
mapping at `0x23A80000` with length `0x96000`, while `hybrid_gui` mapped `0x12C000` (both buffers) in
one call. `hybrid_gui` now maps OSD A (`0x23A80000`) and OSD B (`0x23B16000`) separately, and after
the VPH kick it fills both with solid magenta (RGBA4444 `0xF00F`) for 1.5 s before forcing a full UI
redraw — an on-panel answer to "does the OSD write path work at all?". Build
`artifacts/tests/custom-os-build-19` (`hybrid_gui` `8284b71b…`), initrd `b9767d85…`; the QEMU gate
with it is **PASS** (`qemu-custom-kernel-verify-14`, 71.4 s, six of six). On the camera
(`artifacts/tests/camera-fullos-flash-1`, initrd `2e3e41e6`, TCP drop 3.05 s) the user saw **no
magenta flash and no UI**: with the per-buffer mapping in place, the panel still received nothing
from `hybrid_gui`, so either the process does not run in this boot or the OSD write path is
rejected/not composed even at the captured length.

To get a host channel inside the custom OS (the klog ring does not survive the warm reboot), the
deployed debugger daemon `/setting/a6400_usb_debugd.elf` (20,764 B, the same binary the stock OS
runs as `a6400_usb_debug`) is now started by the loader branch when the cmdline carries
`custom_os.debugd=1`, with `A6400_EPHEMERAL_RAM=1` so its log stays in `/tmp_bt` (no flash write).
Build `artifacts/tests/custom-os-build-19`, initrd `4aaba837…`, bundle cmdline `53fe8d59`
(`custom_os.qemu=1 custom_os.osd=stream custom_os.debugd=1`). The run
`artifacts/tests/camera-fullos-debugd-2` then exposes `ps`, `/tmp_bt`, `dmesg` and `/dev` inside the
running custom OS. In practice the daemon **did not answer** from the custom OS: the probe's first
successful `PONG` was the stock daemon (pid 700) after the reboot (`uptime 24 s`), so the USB
gadget/vendor interface is not established inside the custom OS in that window. Whether the custom
kernel or the missing stock USB userspace is responsible is not yet established.

The display side was then tested **from the stock OS**, which avoids the custom boot entirely:

- A read-only mapping probe of the deployed binary in MSC mode (`--probe-osd`) reports
  `/dev/stream` and `/dev/mem` both map at `0x23A80000`, length `0x96000`, with `O_RDONLY` and
  `O_RDWR`, first pixel `0xdddf` — so the OSD mapping itself is fine on this hardware, and the
  stock VPH is already scanning OSD A (the RAMBOOT dump shows `ch53 ctrl=3 addr=0x23A80000`).
- Running the bridge UI for 12 s in the stock OS (`--bridge --stream`, **no VPH writes**) prints
  `[HYBRID_GUI_READY] bridge shm=0 osd=2 kernel=3.0.27_nl-rt106+` and
  `[HYBRID_GUI] first_frame_us=71897 (draw=20981 conv=45045 osd=5859)`, i.e. the UI code draws and
  presents a frame on the camera.

So the UI code, the OSD device and the drawing path all work on hardware. `hybrid_gui` was then
made robust for the custom boot: it tries the requested OSD device and falls back to the other one,
and a failed VPH kick no longer aborts the UI (the handover inherits the stock VPH state). Build
`artifacts/tests/custom-os-build-22` (`hybrid_gui` `50b55b1e…`), initrd `ae41bd97…`; the camera run
`artifacts/tests/camera-fullos-ui-1` carries it.





## Failures and limits

- The UI presence is currently a **user observation**, not a host capture: the camera is in MSC
  and the Custom OS has no host channel after the jump (the stock debugger is gone), so the panel is
  the only display evidence.
- In MSC the sensor/ISP is in standby (E0149), so a "live view" seen on the panel during a custom
  boot is the SOURCE the custom OS drives (av-cam/replay), not proof of a sensor frame.
- The hardware branch cannot be used for the UI until E0410 is fixed (SDIO/MMC quiesce or a
  different warm-state handling); every hardware-branch run dies before the loader.
- The deadman is the only teardown; each run reboots the camera to stock. Post-reboot states were
  healthy in all runs (fresh `boot_id`, clean `/tmp_bt`, `ramboot` unloaded, stock `liro`/`dmm`).

## Next boundary

1. Read the `camera-fullos-stream-kmsg-1` ring: `[HYBRID_ERR]`/`[HYBRID_VPH]`/`[HYBRID_GUI_READY]`
   name the exact failing step of the UI path (OSD map, shm, VPH kick, or frame drawing).
2. If `hybrid_gui` maps and kicks but nothing appears, compare its frame alpha/format and the ch53
   base with the OSD A/B the panel is scanning (E0298: alpha in the RGBA4444 high nibble; E0652:
   ch53 base and `0x500` pitch), and check whether the replay's OSD surface is the buffer the panel
   actually scans.
3. After the UI path is green in QEMU (regression) and on the camera, the plan's full-OS camera
   step is complete; the hardware branch still needs the E0410 fix.
