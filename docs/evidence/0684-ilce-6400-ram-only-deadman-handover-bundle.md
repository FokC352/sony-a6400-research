# E0684 — ILCE-6400 RAM-only deadman handover of the Custom Kernel #5 bundle (MSC mode)

Date: 2026-11-22. Physical ILCE-6400 in MSC mode, stock 3.0.27_nl-rt106+. Authorized by the user as
a RAM-only test: the payload lives in `/tmp_bt` tmpfs and in RAM page lists, no eMMC/SD/flash write,
no service mode, no firmware install, no driver replacement. The `deadman=90` watchdog is expected
to warm-reboot the camera; that reboot is part of the test.

## Question

Does the packed `artifacts/custom-kernel-ramboot-v1` bundle (custom kernel Image + hybrid initramfs)
survive the full path on the camera: debugger transport, payload staging, `ramboot.ko` dryrun,
RAM-to-RAM handover, custom-kernel boot, and the deadman return to stock?

## Inputs

| Input | Identity (from the launcher receipt) |
| --- | --- |
| custom-Image | 4,421,888 B, SHA-256 `33803be11d1719a72b6f55a2f603bf3a0bf73ba4b61dfcb765b9d405729d26b8` |
| RBINITRD.CPIO | 1,072,790 B, SHA-256 `44d8146585e60ea1aa0a15f96903fee29c37a35d3f20c74449e9891d3f50c8d7` |
| RBCMDLN.TXT | 1,680 B, SHA-256 `ad42d60c0114d5da9de26b86bc6013770526cbbc90aec64c209fe883519006f8` |
| RAMBOOT.KO | 17,420 B, SHA-256 `f01746a38900ce1a2499b544145f1cd2d14e21d374ecd8bd262cc82ca31fc65b` |
| RBMANIF.TXT | 149 B, SHA-256 `b9c043b19e377955996f876e248576f5260702f170e960f20da93b97ec689c97` |
| tcp_recv / tcp_debugd / im_cleanup / vphreg / wifi_up.sh | `6dc5edf5…`, `5af148db…`, `4d1058dd…`, `cdf1305e…`, `1b1e5663…` |
| expected FNV-1a | kernel `ee8ba175`, initrd `8e0d4842`, cmdline `24a85bf2` |

Transport: `a6400_usb_debug` ext-command at first (`PONG protocol=2 pid=700`), then `tcp_debugd`
over Wi-Fi at 192.168.100.17:4640 (`PONG protocol=2 pid=1119 transport=tcp`).

## Commands

```powershell
C:\Python314\python.exe -u custom_os\launcher\launch_custom_kernel_flow.py --mode dryrun --skip-liveview-wait --out artifacts\tests\camera-custom-kernel-dryrun-1
C:\Python314\python.exe -u custom_os\launcher\launch_custom_kernel_flow.py --mode deadman=90 --skip-liveview-wait --out artifacts\tests\camera-custom-kernel-live-1
python -m tools.camera_debugger dump-ring --output artifacts\tests\camera-custom-kernel-live-1\klog-ring.txt --json artifacts\tests\camera-custom-kernel-live-1\klog-ring.json
python -m tools.camera_debugger exec "uname -a; cat /proc/uptime; ..."
```

Python 3.14 is used because the USB ext-command bootstrap needs `pycryptodome` (4.3.0), which the
repository `venv` does not have; this is the same interpreter `launch_custom_os_kernel.bat` selects.

## Results

### Preflight and dryrun [C]

Before the run: `uname` 3.0.27_nl-rt106+ #1, uptime 1068.84 s, `MemFree 19,088 kB`,
`boot_id 616c5678-162c-414a-a740-cf9823af3537`, `/tmp_bt` with Sony files only, no `ramboot`,
`dmm_rule [0000]`, `/system/vmlinux.bin` and `/usr/kmod/liro.ko` present.

The dryrun brought Wi-Fi up over USB (35 s budget), started `tcp_debugd`, streamed both files
(`TCP_RECV_OK bytes=4421888 fnv=ee8ba175`, `TCP_RECV_OK bytes=1072790 fnv=8e0d4842`), staged them in
the 12 MiB `/tmp_bt/stage` tmpfs, and `insmod`ed `RAMBOOT.KO` in dryrun mode:
`dryrun: payload verified and staged; no handover.`, `INSMOD_RC=125`, `ramboot` not in
`/proc/modules`. Receipt: `artifacts/tests/camera-custom-kernel-dryrun-1/result.json`.

### Live `deadman=90` handover [C]

`artifacts/tests/camera-custom-kernel-live-1/result.json`: dryrun passed again, then the live
trigger `insmod /tmp_bt/stage/RAMBOOT.KO`; the TCP debugger answered at t=0.047 s and was gone at
`t=3.052 s` (`handover_tcp_dropped: true`, `dropped_at_s: 3.052`). No `hybrid_gui`/`imlaunch` was
running before the jump (`procs_before_stage` lists only `tcp_debugd`), so nothing was killed for
the handover.

### Warm-reboot klog ring: the custom kernel booted, then the deadman returned to stock [C]

`dump-ring` returned a valid ring (CRC32 `0x36b23117`, `boot_count` 4,
`artifacts/tests/camera-custom-kernel-live-1/klog-ring.txt`). In this session's boot the records
are, in order:

```
[ 156.277710] [RAMBOOT] start: dryrun=1 fault=0x000000 deadman=0s
[ 187.037325] [RAMBOOT] start: dryrun=1 fault=0x000000 deadman=0s
[ 188.678508] [RAMBOOT] start: dryrun=0 fault=0x000000 deadman=90s
[ 188.827635] [RAMBOOT] committing handover: trampoline 640 bytes at PA 0x07a00000
[ 189.496540] Brought up 4 CPUs
[ 190.436324] RAMDISK: gzip image found at block 0
[ 194.723694] LIRO (liro) rev:4-MUSASHI
[ 194.727519] Param:adrs=00000000, debug=0, delay=0
[ 194.732501] Address not set, firmware will no be loaded!
[   0.790557] UDIF: resume start: wdt
```

The two `dryrun=1` records are the standalone dryrun and the launcher's mandatory preflight;
`dryrun=0` is the live jump. The new kernel then reports 4 CPUs, finds the gzip ramdisk and reaches
the hybrid init's `liro.ko` load ~6 s after the jump. The final `UDIF: resume start: wdt` at 0.79 s
is the deadman warm reboot to the stock OS. The ring's logger clock restarts on a warm resume, so
its numbers are not `/proc/uptime` (which read 1,135.94 s just before the run).

### Post-reboot state [C]

`post-reboot-state.txt` in the same receipt directory: new
`boot_id 9b1eaab9-28ef-4704-bbb4-71a12d44d899`, uptime 175.05 s, `MemFree 20,288 kB`,
`/tmp_bt` back to Sony files only (the staged payload was RAM and is gone), `ramboot` not loaded,
stock `liro`/`dmm` present, `dmm_rule [0000]`, `/system/vmlinux.bin` and `/usr/kmod/liro.ko`
unchanged. The debugger answers over the ext-command transport again.

## Failures and limits

- This is the **hybrid** bundle (`RBINITRD.CPIO`, its own `/init`), not the full
  `custom-full-initrd.ext2.gz` Custom OS. The ring shows the hybrid init loading `liro.ko` with
  `adrs=00000000` ("Address not set, firmware will no be loaded!"), i.e. the hybrid UI ran without
  the av-cam backend. The full-OS hardware path is not covered by this record.
- No LCD frame was captured during the ~90 s custom-OS window; the evidence is the console ring,
  the TCP drop and the post-reboot state, not a panel image.
- The camera bundle keeps the initrd's hardware branch; its warm Wi-Fi/SDIO bring-up fails in QEMU
  (E0410 mmcio), and the camera run did not test that branch (the hybrid init does not run Sony's
  `/sbin/init`).
- One run only, as the plan requires; the ring also retains three earlier handovers from previous
  sessions, which is why the same `[RAMBOOT]` shapes appear more than once.
- The QEMU gate's declared lens exception (six `ObjCamera` commands answer `0xffff` without a lens)
  is unchanged; this hardware run does not exercise that replay.

## Next boundary

1. Build a camera bundle from the full `custom-full-initrd.ext2.gz` and run the same dryrun +
   `deadman=90` path, after the hardware branch's warm Wi-Fi/SDIO failure is handled (quiesce/reset
   in `ramboot.ko`, or proof that the camera differs).
2. Capture the hybrid UI frame (LCD) during the window to complement the console ring.
3. A lens protocol model (or an explicit no-lens replay) to clear the six `o21` rejects.
