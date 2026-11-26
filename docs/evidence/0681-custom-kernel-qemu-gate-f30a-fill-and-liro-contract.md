# E0681 — Custom Kernel #5 QEMU gate: 0xF30A fill model, im.elf LIRO load contract, Phase A/B harness

Date: 2026-11-22. Offline QEMU only (no camera, USB, or SD access). The custom kernel and initrd
are RAM payloads built from the captured ILCE-6400 kernel and the stock initrd; the camera's
firmware and `/setting` capture were never modified.

## Question

`launch_custom_os_kernel.bat` hands over from the stock OS to Custom Kernel #5
(`custom-Image` + `custom-full-initrd.ext2.gz`) through `ramboot.ko`. Two things had to be
established before any camera test:

1. does the QEMU board model the `0xF30A` memory-fill engine that av-cam's `entry_pre` runs before
   its work-DMA copy, so a RAM-to-RAM handover does not start from stale `.bss`?
2. does the Custom OS loader follow `im.elf`'s own LIRO contract (index 37, UPM pre-LIRO sync,
   `Backup_read(0x00fa007c)` → `adr=`), and does `av-cam` then reach `dmmrule(5) = 0`, the full
   replay, `vdf_unmute`, and LiveView, in the full hardware model?

## Changes

- `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c`: opt-in `f30a-fill=on` model. A 0x2000 container at
  `0xF30A0000` whose only leaf is the `+0x1280` window (CTRL `+0x00`, STATUS `+0x04`, bytes at
  `+0x20`/`+0x24`, four fill words at `+0x2C..+0x44`, window-relative). `CTRL=0x20` prepares,
  `CTRL=0x21` executes a synchronous zero fill of `[DST_PA, DST_PA + BYTE_LEN)` through
  `address_space_write` in 4 KiB chunks and logs it under `-d unimp`; busy reads 0; a non-zero fill
  word, a zero/oversized length or an unaligned destination is refused and logged (E0642 records no
  non-zero fill, alignment or interrupt semantics). Priority `-100` keeps the AIF groups (`-300`)
  and the unmodelled catch-all (`-1000`) below it; every other address in `0xF30A0000..+0x1FFF`
  still reaches its own model.
- `qemu-a6400/run.sh`: `F30A_FILL=1` adds `f30a-fill=on`; declared `KERNEL`/`INITRD` overrides boot
  a custom pair while the captured inputs stay verified, rewrite the cmdline's `initrd=0x...,size`
  token to the file's real size, and print each override's SHA-256; the machine string is echoed;
  `ULOGIO` staging uses `fat_add_file.py --if-absent`, which accepts an existing entry only when its
  size matches and never overwrites.
- `custom_os/launcher/imlaunch.c` (private): `liro_load=0|1`; for type `0x10000002` it always adds
  `adr=0x%x` from `Backup_read(0x00fa007c)` (falling back to `0x00fa0076`, then the E0031 PA
  `0x035c6000`) after the UPM pre-LIRO sync `0x00920114`, and with `liro_load=1` appends
  `fn=/system/av-cam.bin load=1 debug=0`.
- `custom_os/hybrid/build_full_initrd.py` (private): both `custom_start.sh` branches no longer
  `insmod liro.ko` early; `imlaunch` owns it with `liro_load=1`.
- `custom_os/hybrid/run_qemu_custom_kernel_verify.py` [new, private]: Phase A gate over
  `qemu-a6400/run.sh` with the full model.
- `custom_os/hybrid/gate_checks.py` [new, private]: the shared PASS markers.
- `custom_os/ramboot/run_handover_test.py` (private): new `custom_kernel_full` case for Phase B.

## Inputs

| Input | Identity |
| --- | --- |
| QEMU | pinned 11.1.3 tree `~/sony-a6400-qemu-11.1.3`, overlay from `qemu-a6400/qemu-overlay`, rebuilt with `ninja qemu-system-arm` |
| custom kernel | `artifacts/custom-kernel-ramboot-v1/custom-Image`, 4,421,888 B, SHA-256 `33803be11d1719a72b6f55a2f603bf3a0bf73ba4b61dfcb765b9d405729d26b8` |
| custom initrd | `custom_os/build/hybrid/custom-full-initrd.ext2.gz`, 1,073,718 B, SHA-256 `43f20544ef8b5b3ad3e8d8e070f60f13785ac704bef3fd228c21d5af8e5da7f9` (ext2 3,145,728 B, `replay-min5-nolens-v1.txt`, `hybrid_gui --vph-kick`, OSD path selectable; receipts `verify-12` and `custom_kernel_full -5` use it) |
| replay fixture | `custom_os/launcher/replay-min5-nolens-v1.txt`, 13,549 B, SHA-256 `9365d805fb5cb14a0d857bbd57e24666403ae4b36af28724a5622c29816069e0` (168 commands; derived from `replay-min5-v2.txt` `939d6cf8…41eab` by removing nine lens-drive lines) |
| launcher | `artifacts/tests/custom-os-build-14/imlaunch`, 30,248 B, SHA-256 `0dd86675222efffcd9919bee1d1fdc5766972873d06f5c82bad20c44a4a87ded` |
| av-cam | captured ORIL `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`, 16,803,048 B, VA base `0x635C6000` |
| prepared inputs | `~/a6400-inputs/inputs-moddev-1` (emmc 2 GiB, `18e37ec3…cc94b`); its `/setting` already carries the camera's own `ULOGIO.BIN` (3,772 B) |
| sub-micro frames | `artifacts/qemu-a6400/capture-20261117-launcher-test/run with spaces/submicro-frames.bin`, SHA-256 `6d5026e3…fa85ed` |

## Commands

```bash
cp -R qemu-a6400/qemu-overlay/. ~/sony-a6400-qemu-11.1.3/ && ninja -C ~/sony-a6400-qemu-11.1.3/build qemu-system-arm
bash custom_os/launcher/build_wsl.sh artifacts/tests/custom-os-build-14
python3 custom_os/hybrid/build_full_initrd.py --build-dir artifacts/tests/custom-os-build-14
python3 custom_os/hybrid/run_qemu_custom_kernel_verify.py --out artifacts/tests/qemu-custom-kernel-verify-9
python3 custom_os/ramboot/run_handover_test.py --build artifacts/custom-kernel-ramboot-v1 \
    --out artifacts/tests/qemu-ramboot-custom-full --case custom_kernel_full
```

Phase A ran `run.sh` with `WORK_DMA=1 F30A_FILL=1 CAMPIG_ENABLE_READBACK=1 SGC1_EVENT_A=1
DDRB_CONTROL=1 DFS_CONTROL=1 LOWMEM_ABORT=1 WATCHDOG=1 REAR_CONTROLS=1 LIVEVIEW_SOURCE=bars
EVSEL_LATE_US=500 ZMA1_STARTUP=1 ZMA1_JPEG_FILL=1`, `SUBMICRO_FRAMES` from the verified extract,
`KERNEL=custom-Image`, `INITRD=custom-full-initrd.ext2.gz`, `EXTRA="custom_os.qemu=1"`,
`-icount shift=0,sleep=on -action watchdog=pause -qmp unix:… -d unimp,guest_errors`
(`--no-qemu-log` for the later runs; the fill receipt is from `verify-5`).

## Results

### 1. The 0xF30A fill model executes the captured zero fill [C]

`-d unimp` records one prepare and one fill from av-cam `entry_pre`:

```
cxd90045.f30a-fill: write +0x0 size 4 <- 0x20       (CTRL prepare)
cxd90045.f30a-fill: write +0x20 size 4 <- 0x4052000 (DST_PA)
cxd90045.f30a-fill: write +0x24 size 4 <- 0x49d625  (BYTE_LEN)
cxd90045.f30a-fill: fill #1 0x04052000 + 0x49d625 bytes zero (total 4838949)
```

No fill was refused. The captured ORIL build differs from the packaged image E0642 measured
(0x0403A000 + 0x497A26), so the model records the build's own values. The work DMA then runs 53
copies for 853,912 bytes (first `0x044f1000 -> 0x045cc500`, 16,384 B), i.e. the fill and the copy
belong to the same routine, as E0642 described.

The first gate runs (verify-1..4) show why the model was needed: with the untested window-relative
offsets the engine was addressed correctly by the guest but the model did not act; the guest's
prepare/CTRL/STATUS sequence is now visible in the log, which is the receipt that the region, not
the AIF group, serves `0xF30A1280`.

### 2. The im.elf LIRO contract is followed [C]

```
IML liro adr unit 0xfa007c rc=4 value=0x35c6000
[    2.672249] LIRO (liro) rev:4-MUSASHI
[    2.672279] Param:adrs=035c6000, debug=0, delay=0
IML  37 kmod /usr/kmod/liro.ko args=' adr=0x35c6000 fn=/system/av-cam.bin load=1 debug=0' insert rc=0
[    2.729141] start entry_pre(635c6044)
[    2.733883] end entry_pre
IML dmmrule(5) = 0
IML replay done: 177 commands, 6 problems (the lens-only set below)
IML vdf_unmute setYUVMute=0 setPanelOut=0
```

`libbackup`'s wrapper returns status 4 while still writing the preset PA (E0032), so the value, not
the status, is what im.elf formats; the launcher logs both and only tries the second unit
`0x00fa0076` when the first returns zero. `liro.ko` accepts the argument and prints
`Param:adrs=035c6000`. LIRO is loaded at IMDB index 37 after the infrastructure inits and the
`0x00920114` UPM sync, and `dmmrule(5)` answers 0 (it returned `-4` in the earlier ad-hoc
`run_qemu_full_test.py` run, which loaded `liro.ko` early with `insmod` and no `adr=`).

### 3. Replay: the captured fixture rejects six lens commands; the gate runs a no-lens fixture [C]

The captured fixture (`replay-min5-v2.txt`, SHA-256 `939d6cf8…41eab`) was recorded on a camera with a
lens attached. Running it on the board (lens rail modelled, no lens protocol) executes all 177
commands and returns six non-zero replies, all `ObjCamera` (`o21`):

| Line | Command | Rejections |
| --- | --- | --- |
| 271 | `I S o21 3100 651e` | 1 |
| 361, 367, 381 | `I A o21 3100 7578` | 3 |
| 369, 383 | `I A o21 3100 768c` | 2 |

Three other lines with the same ids (261 `651e`, 295 `7578`, 363 `768c`) are accepted, so the
rejects are state-dependent, not a simple per-id capability. The ids are the aperture/focus/zoom
drive group [I]; there is no local record of their reply contract.

The plan's criterion is "0 problems", and the board has no lens. The gate therefore uses
`replay-min5-nolens-v1.txt` (SHA-256 `9365d805…069e0`, 13,549 B): the same captured fixture with
exactly the nine lens-drive command lines removed (`scratch/make_nolens_fixture.py` lists them;
no wait, blob, comment or other command changes), re-baselined to **168 commands**. Every remaining
command must succeed — `gate_checks.KNOWN_LENS_REJECTS` is empty, so any failure fails the gate.

`artifacts/tests/qemu-custom-kernel-verify-11/result.json` is **PASS** with
`commands: 168, problems: 0, failing_commands: [], unexpected_failures: []`. The 177-command
receipt (`verify-10`, six declared lens rejects) remains preserved as the previous baseline.


### 4. LiveView, the panel OSD and the QMP key path all work [C]

The AIF live-view path works: the guest arms RC0 and the model fills the alternating Y8 planes

```
[AIF-LV] ch+0x380 -> PA 0x14e90000 lb=0x400 skip=0x80 lines=570 sample0=0xeb
[AIF-LV] ch+0x380 -> PA 0x14fbc000 ...
```

The first Phase A runs (verify-5, verify-6) still ended with `en144=0x01000231`, `ch53 ctrl=0` and
black screendumps. The cause was the QEMU branch of `custom_start.sh`: it started
`hybrid_gui --bridge --osd` while the hardware branch already passed `--vph-kick`. `--vph-kick` is
the Custom OS display driver: it programs VPH ch53 (`0xF3441300`) from `OSD_A_PA 0x23A80000`, clears
the `0x140` mute bits, sets `0x144` to `0x01000233` when ch51 is running, and keeps re-arming both
in a child process. With the same flag in both branches (uploaded in initrd
`f7b2e970…`, 1,073,618 B), Phase A runs `verify-7..10` give the plan's panel values:

```
[HYBRID_VPH] bridge vph_kick=1 ch53_pa=0x23a80000 0x140=00000000 0x144=01000232
VPH clk=00000111 en144=01000233 osdbpp=00000000 | ch51 ctrl=00000002 st=00000000
    addr=fe060000 lb=00000400 skip=0x80 lines=0x239 | ch53 ctrl=00000002 addr=23a80000 lb=0x500 lines=0x1df
```

`en144=0x01000233` and `ch53 addr=0x23a80000` match the plan exactly, ch51 alternates between two
base buffers across samples (`fe060000`/`fe16a400`), and both VPH screendumps are non-blank with a
changed frame after the keys (`lcd-custom-os.png`, `lcd-custom-os-keys.png`).

The plan's ch51 `ctrl=00000003` is observed (`verify-11`: `ch51_ctrl: 00000003`, sampled while the
channel was started), but it is not a stable register state: E0639 records the AIF `+0x00` bit 0 as
the start/busy bit cleared at frame end, E0652 records the captured stock channel at `ctrl=0x2`
between frames, and E0676 records VPH clearing AIF start bits at vsync. The gate therefore checks
the enable bit plus two alternating buffers and records the raw sample instead of one instant.

`st=00000001` is **not** produced and is not implemented. No local record documents the AIF `+0x04`
status word's bit 0; the only recorded AIF contract is the `+0x00` start/busy bit, and the guest
image contains no `0xF3441204` literal (a static scan found no direct status read). Writing a 1
there would be an invented register value, which the fidelity rules forbid. E0681 therefore records
this sub-item as an open deviation awaiting either captured `+0x04` evidence or an explicit user
waiver; the current gate item is the semantic LiveView check.


The QMP key failure was in the gate's own stimulus, not in the model or the guest:

- QEMU 11's `send-key` takes QKeyCode objects (`{"type": "qcode", "data": "ret"}`); plain strings
  are rejected with "expected: object" (first attempts).
- The early `ui_item` then sent an additional `input-send-event` down/up pair for the same key.
  `sony_a6400_rear_key` sets ADC channel 3 to 45 on down and back to 1023 on up; the ADC model
  stores the level without an interrupt, so the guest must sample it (~100 Hz unit-1 scan behind
  `liro-216`, plus input.ko's 10 ms key-table timer). The pair released the channel within the same
  millisecond, before either sampler ran — the press was cancelled even though QMP reported
  success.
- The subagent's A/B receipt (`artifacts/tests/custom-kernel-key-diag-2/result.json`) shows the
  same boot sampling `0x002d` while the key is held and `0x03ff` after the pair, with
  `key_events` 1 for a plain `send-key` and 0 for the cancelled one; `custom-kernel-key-diag-1`
  records `Ch1:Port 3 002D`, live `liro-216` counts (1327→1372→1582) and
  `kbd send osal type:1 code:1ac value:1`.

`ui_item` now sends only `send-key` with `hold-time 180` (the stock `tools/rear_panel.py` path) and
records the QMP replies. `artifacts/tests/qemu-custom-kernel-verify-10/result.json` is **PASS**
(`elapsed_s 80.6`, `key_events 2`, `qmp_errors []`), with `IML key cb=3 code=0x15010050` (ret →
center) and `0x15010060` (up → DISP) consumed by `hybrid_gui` (`IML ui_set`, `[HYBRID_GUI] key=o/u`).



### 5. Phase B (stock OS → Custom Kernel #5): handover works; the hardware Wi-Fi branch is the blocker [C]

`custom_kernel_full` boots the captured stock OS to its UI, stages `custom-Image` +
`custom-full-initrd.ext2.gz` with a fresh manifest, runs `RAMBOOT.SH`, and hands over. The QEMU
case uses `deadman=900` (not the plan's 90) because the gate needs more than 90 s of guest time
after the jump; the camera test keeps `deadman=90`.

The run reaches and commits the handover. After `RAMBOOT.SH` stops Sony's userspace, `MemFree`
rises from 1,248 kB to **93,228 kB**, the payload stages and verifies, and the trampoline jumps:

```
[RAMBOOT-SH] stopped 145 im.elf by TERM (pass 1); user memory released, kernel exit pending …
[RAMBOOT-SH] MemFree:           93228 kB
[RAMBOOT] staged /setting/RBKERN.IMG: 4421888 bytes -> PA 0x00108000 (fnv 0xee8ba175)
[RAMBOOT] staged /setting/RBINITRD.IMG: 1073617 bytes -> PA 0x00700000 (fnv 0xddee2271)
[RAMBOOT] staged /setting/RBCMDLN.TXT: 1680 bytes -> PA 0x00103000 (fnv 0x9131a051)
[RAMBOOT] staged 1344 pages, 0 rejected …, online CPUs 0xf
[RAMBOOT] vph ch53: ctrl=00000003 run=00000000 addr=23a80000 … pitch=00000500
[RAMBOOT] committing handover: trampoline 640 bytes at PA 0x06d12000
```

The new kernel boots (`reserve area 0..7`, 4 CPUs, `VFS: Mounted root (ext2) on device 1:0`,
`RAMDISK: gzip image found at block 0`, `nflasha: nflasha1 … nflasha23`), the stock `/sbin/init`
runs, and `custom_start.sh` starts. It takes the **hardware** branch because the staged cmdline was
rebuilt from the stock `kemco.txt` and did not carry the cold boot's `custom_os.qemu=1`:

```
[CUSTOM_OS_INIT] environment=hardware
```

That branch brings up Wi-Fi, and the proprietary SDIO path fails on the warm card/host state
(`mmc_hwip_system_off`, `mmcio_mount failed!`, `mmcio_wrapper_init failed!`, then DHD power-up
errors). The `execute_tuning` warnings are the Sony AL eMMC driver's non-HS200 messages; they also
appear in the loader branch (1,340 after the jump) and are harmless there, so they are not the
blocker by themselves. The kernel eMMC enumerated all `nflasha` partitions in both branches.

With `custom_os.qemu=1` (runs `artifacts/tests/qemu-ramboot-custom-full-3`, `-4`, `-5`) the gate
reaches the same state as Phase A on top of the RAM handover. `-3` was green on five items and failed
`ui` only because the case omitted `REAR_CONTROLS=1` (`Input handler not found for event type key`);
`-4` was the first full gate on the 177-command fixture. `-5` is the final receipt with the no-lens
fixture:

```
verdict: OK   environment_branch: qemu
iml.log: IML replay done: 168 commands, 0 problems, 12029 ms
         IML vdf_unmute setYUVMute=0 setPanelOut=0
vphreg:  en144=01000233 | ch51 ctrl=00000003 st=00000000 addr=fe16a400 | ch53 ctrl=00000003 addr=23a80000
gate_items: liro_load true, dmmrule5 true, replay true, vdf_unmute true, vph true, ui true,
            marker_source true; watchdog_quiet true
trampoline: stage 4, 1344 pages, quiesce 34 ok / 2 timeouts, parked 0x50415201..03
```

So Phase B proves the same six items through `ramboot.ko` (stock OS → custom kernel → loader →
LiveView → OSD → keys) with the same full model, including the 0xF30A fill and work DMA, and the
replay is literally 0 problems in both phases.

The run also showed why the earlier hardware-branch attempt failed: `-2` (no `custom_os.qemu=1`)
reached `[CUSTOM_OS_INIT] environment=hardware`, then the Wi-Fi/SDIO chain failed
(`mmcio_mount failed!`, `mmcio_wrapper_init failed!`, DHD power-up retries). The QEMU `custom_kernel_full`
case therefore appends `custom_os.qemu=1` to its staged cmdline and the camera bundle keeps the
hardware branch; that warm-state Wi-Fi behaviour (E0410) is a camera-only boundary and is not
claimed here.

The same boundary was then confirmed on the physical camera with the full OS bundle
(`artifacts/tests/camera-fullos-live-1`): the handover commits, the custom kernel mounts the full
initrd, and `liro.ko` loads av-cam with the correct address (`Param:adrs=035c6000`,
`Load firmware /system/av-cam.bin to 635c6000`, `entry_pre`/`entry_main`, `bss 63fa1734 + 0049d625`).
The hardware branch then starts the Wi-Fi bring-up and faults in the `irq/221-mmc1` thread
(`vector exception (0x000) at 0x00000000`, modules include `mmcioWrapper`); the exception monitor
warm-reboots the camera before imlaunch. So E0410 is not a QEMU artefact, and the full OS needs the
loader branch (or an SDIO quiesce/reset) on hardware.






## Check

```bash
python -m unittest tests.test_fat_add_file -v        # new --if-absent contract + strict refusal
python3 -m unittest tests.test_qemu_f30a_fill -v     # WSL: five native qtest cases, all pass
python -m unittest discover -s tests -q              # 2,577 tests, 2 failures, both local baselines
python -m tools.research.render_research_status --write   # claim counts moved to 289/46 after E0681/E0684
python -m tools.research.knowledge_map build
```

The suite runs 2,577 tests. Two failures are local-environment baselines, not this change:
`emulator.test_run_b26_uvc_prearm` pins `camera-daemon/build/a6400-lived` at 632,812 B
`1eda8418…` while the local build is 856,320 B `c92403f6…`, and `test_mod_manager` finds the
untracked `Sony-PMCA-RE` checkout that its constructed root expects to be absent. The seven
research-status/dashboard failures seen before the regeneration were the stale generated SVG (the
map now has 289 claims, and the F02 title changed); `render_research_status --write` fixed them and
they re-run green.

`tests/test_fat_add_file.py` covers `--if-absent`: an existing entry with the same size is accepted
and reported, a different size is refused, and a plain add still refuses any existing name.
`tests/test_qemu_f30a_fill.py` (five native qtest cases) checks the fill model directly: the zero
fill clears a dirty buffer, a non-zero fill word, a zero length and an unaligned destination are
refused, and the default machine keeps the engine unmodelled.

## Failures and limits

- The QEMU gate is green: Phase A (`verify-11`: six of six items, 168 commands / 0 problems,
  `key_events 2`) and Phase B (`custom_kernel_full -5`, same fixture). The replay no longer carries
  an exception list; the captured 177-command fixture still reports its six lens-drive rejects
  separately (section 3). No further camera run is authorized by this record.
- The plan's ch51 `ctrl=00000003 st=00000001` cannot be produced by a model that completes
  transfers synchronously; the gate records the enable bit and two alternating buffers instead.
- Phase B's loader branch is selected with `custom_os.qemu=1`; the camera bundle keeps the hardware
  branch, whose Wi-Fi/SDIO warm-state behaviour (E0410) is not covered by any QEMU verdict.
- The camera's own `ULOGIO.BIN` (3,772 B) is used from the capture; the ZV-E10 stand-in (3,796 B) is
  a different file and is not staged into a prepared capture.
- `-d unimp` over the 56 MB of unmodelled MMIO costs a 27 MB–250 MB log and slows the run; the
  Phase A receipts without it complete in 70–110 s including the VPH poll.

## Next boundary

1. The camera bundle keeps the hardware branch, whose Wi-Fi/SDIO bring-up fails on the warm card
   state (E0410 mmcio). Before a camera handover, either quiesce/reset the SDIO/MMC host and card in
   `ramboot.ko` before the jump (highest fidelity, highest risk) or prove the camera's warm state
   reaches the same mmcio failure and handle it. The QEMU gate does not cover this branch.
2. A lens protocol model (or an explicit "no lens" replay variant) is needed to make the six
   `o21` commands answer 0; do not special-case them in `ObjCamera`.
3. Re-run Phase A and Phase B after those changes; only a fully green gate plus a `deadman=90`
   bundle justifies the single camera test.

