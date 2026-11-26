# Finalized plan — Full Custom OS with LiveView + animated UI on the ILCE-6400

Status: active implementation plan (2026-11-22). Follows E0685 and the last four camera runs.

## Evidence base

| Claim | Receipt |
|---|---|
| `/dev` is the initrd's read-only static directory: `can't create /dev/kmsg: Read-only file system` for `custom_start.sh` lines 31/41/44 and 47 | `artifacts/tests/qemu-custom-kernel-verify-14/serial.log` |
| No printk at all for ~84 s after `LIRO … entry_main` until the deadman; the UI never reported | `artifacts/tests/camera-fullos-stream-kmsg-1/klog-ring.txt` |
| Hardware clears the AIF `ch53` start bit every frame, so the OSD must be re-armed at 60 Hz | `custom_os/docs/evidence/0648-ilce6400-panel-display-contract.md` §3 |
| `3200/2030`, `3200/204a`, `3200/2048`, `3200/2047` reprogram the OSD surface during the replay (the resulting `ch53` base is not recorded there) | `custom_os/launcher/replay-min5-nolens-v1.txt` |
| OSD A/B live at `0x23A80000` / `0x23B16000`; `im.elf` maps them through `/dev/stream`, and CPU copies into those mappings appeared on the panel | E0298, E0653 |
| `0x140 = 0x30000000` is normal display-pipeline status, not a mute: the stock OS reads exactly that while Sony's own UI is fully visible (`en144=01000232`, ch51 `14e90000`, ch53 `23a80000`) | stock-OS `vphreg` probe 2026-11-23 (camera, read-only) |
| `/dev/mem` without `O_SYNC` is Normal-cacheable, with `O_SYNC` it is non-cacheable | `docs/evidence/0647-…devmem-reboot-root-cause-and-policy.md` §2 |
| DRAM `/dev/mem` is FORBIDDEN on the camera without a per-address go-ahead; `/dev/stream` is the proven OSD path | `tools/devmem_policy.py`, E0647 policy table, E0074 |
| `hybrid_gui` page fault at `0x00367000` was a **read** past `bb`: `present()` scanned 4 pixels at a time with `i < W` while `W = 854`, so the last row read `bb[W*H]`, exactly `_end`/mapping end (`pc 0x195e0`, `ldr ip,[r3,#8]`, `r3 = bb + 479*3416 + 3408`) | `artifacts/tests/qemu-custom-kernel-verify-15/serial.log`, `artifacts/tests/hybrid-debug-1/` (addr2line, nm, disassembly) |

## Root causes

1. **No writable device nodes.** `/dev` is read-only in the custom boot, so `kmsg()` and the OSD
   opens silently fail; every `[HYBRID_*]` marker was lost.
2. **Race between `imlaunch` and `hybrid_gui` [I].** `hybrid_gui --bridge` starts 4–6 s in, while
   `imlaunch` is still replaying `2030`/`204a`/`2048`/`2047`, which reprogram the OSD surface. The
   replay file shows the commands but not the resulting `ch53` base; the takeover is an inference
   until a run logs the observed `ch53 addr`. The kicker logs it, and the handshake is fail-closed.
3. **Missing 60 Hz `ch53` re-arm in `--flash-only`.** The bridge path already kicks the start bit
   every millisecond, but it kicks whatever buffer `av-cam` last programmed, and `--flash-only` had
   no kicker at all. Both paths now program the descriptor and kick.
4. **OSD mapping and cacheability [I].** `/dev/stream` without `O_SYNC` may be Normal-cacheable;
   `O_SYNC` is requested on both devices. E0653 shows CPU copies into `im.elf`'s `/dev/stream`
   mappings reaching the panel, so `/dev/stream` is the camera path. DRAM `/dev/mem` stays off the
   camera (policy) and is used only inside QEMU, where no such policy applies.
5. **Row-scan over-read in `present()` [C].** The transparent-row scan stepped 4 pixels at a time
   with `i < W`; since `W = 854` the last iteration read `src[854]`/`src[855]`. On the final row that
   is one element past `bb`, which is the last `.bss` object and ends exactly at `_end`, so the read
   faults at `0x00367000`. Fixed by `i + 3 < W` plus a scalar tail loop; this is why Phase A died
   right after `[HYBRID_GUI_READY]` while its VPH registers were already correct.
6. **Replay duration is not stable on hardware [C].** Measured handshake times after the jump: ~10 s
   in one boot and ~192 s in another (QEMU: ~13 s); the 168-command replay blocks on event waits whose
   completion depends on av-cam's state. A wait-for-replay gate is therefore unusable on the camera:
   the first 50 s budget never fired the UI, and a 300 s deadman expired exactly at a 192 s handshake.
   `imlaunch` now publishes `cam_ready` right after LIRO/act/dmmrule (early) and the UI re-asserts
   `ch53` every millisecond while the replay runs; `IML replay done`/`replay_status` still report the
   replay outcome separately.
7. **`0x140=0x30000000` is not a mute [C].** A read-only stock-OS probe shows the same value while
   Sony's UI is visible, so the plan's "clear the mute bits" assumption is refuted; the gate records
   the value and only rejects the two top bits. The steady-magenta bridge probe (`--magenta`,
   `custom_os.magenta=1`) now decides whether `ch53` is composed at all once av-cam owns the panel.

## Work items (dependency chain W1 → W2 → W3)

### W1 — handshake flag (`custom_os/hybrid/ui_bridge_shm.h`, `custom_os/launcher/imlaunch.c`)

- Add `volatile uint32_t cam_ready;` after `cam_seq`; reset it in `ui_attach()`.
- Make `run_replay()` return its problem count and use `run_vdf_unmute()`'s return value.
- Publish only when both are clean: write `cam_ready = 1`, create `/tmp_bt/cam_ready` (content `1`),
  print `IML cam_ready 1`, and write it through kmsg with a `<1>` priority (`/dev/kmsg`, else
  `mknod /tmp_bt/dev/kmsg c 1 11`). Otherwise log `IML cam_ready 0 (replay=<n> vdf=<rc>)` and leave
  the flag absent, so both waiters fail closed on a broken replay.
- The flag file is what the init script waits on; the console/kmsg copies keep the marker visible
  in `serial.log` and in the camera klog ring.

### W2 — `custom_os/hybrid/hybrid_gui.c` (depends on W1)

- `kmsg()`: try `/dev/kmsg`, then `mknod` + write `/tmp_bt/dev/kmsg` (`c 1 11`).
- `osd_open(name, major, minor, sync)`: try `/dev/<name>`, then `/tmp_bt/dev/<name>` after `mknod`
  (`mem` `c 1 1`, `stream` `c 249 7`, `kmsg` `c 1 11`, `stream2` `c 249 8`, `uipc` `c 251 0`).
  `O_SYNC` is requested on both devices; the device is selected by the `--stream` / `--osd` flag.
- Single `vph_program(v, osd_pa, yuv_live)` writing `0x028`, `0x404=0`, `0x1320/0x1324/0x132C/0x1348`,
  `0x140 &= ~0x30000000`, `0x144 = (yuv_live ? 3 : 2)`.
- `vph_kick(v)`: `if (!(ctrl & 1)) ctrl = (ctrl & ~6u) | 3u`.
- Kicker child: every 1 ms `vph_kick`; every 500 ms re-assert `vph_program` and log the observed
  `ch53` base, so an `av-cam`/ObjRenderer takeover is both repaired and visible in klog.
- `--flash-only`: magenta into both OSD buffers with the kicker running; logs
  `[HYBRID_SH] flash-only kicks=<n>` (n ≥ 60) before clearing.
- `--bridge`: bounded wait (30 s) for `ui_shm.cam_ready`. **Fail closed:** on timeout log
  `[HYBRID_ERR] cam_ready timeout` (stdout + kmsg) and exit non-zero without drawing. Otherwise
  program VPH and draw immediately; the blocking magenta hold is gone (kept only behind
  `--magenta`).

### W3 — initrd sequencing (`custom_os/hybrid/build_full_initrd.py`; depends on W1)

- `custom_start.sh`: create `/tmp_bt/dev` nodes (`mem`, `kmsg`, `stream`, `stream2`, `uipc`, `null`,
  `console`) and write every marker to `$DEV/kmsg` (no `> /dev/kmsg`, which is EROFS in the initrd).
- **Keep** the QEMU branch's `imlaunch` stdout on `/dev/console`: `gate_checks.py` reads every replay
  marker from `serial.log`, so redirecting it to a file would break the gate. The hardware branch
  keeps `/tmp_bt/iml.log`.
- OSD device: `--osd` only when `custom_os.qemu=1` is on the cmdline; `--stream` otherwise. The
  camera therefore never touches DRAM `/dev/mem` for the OSD.
- Wait (bounded 50 s) for the flag file `/tmp_bt/cam_ready` before `hybrid_gui --bridge …` in both
  branches; on timeout log `[CUSTOM_OS_SH] cam_ready timeout` and do **not** start the UI.

### W4 — verification

1. WSL build (`custom_os/launcher/build_wsl.sh`) into `artifacts/tests/custom-os-build-25b`, plus a
   copy of `artifacts/tests/wifi_up.sh`; then rebuild the initrd from that directory:
   `python3 custom_os/hybrid/build_full_initrd.py --build-dir artifacts/tests/custom-os-build-25b`
   (the gate refuses a `custom-full-initrd.ext2` older than `imlaunch`). Record binary, `mwftap.so`,
   `wifi_up.sh` and initrd hashes in `artifacts/tests/custom-os-build-25b/sha256.txt`.
2. Gate check first: add a `cam_ready` item to `custom_os/hybrid/gate_checks.py` that requires
   `IML cam_ready 1` and is part of the PASS verdict; the runner passes the serial buffer it already
   has. Then QEMU Phase A (`run_qemu_custom_kernel_verify.py --build-dir
   artifacts/tests/custom-os-build-25b`, out `artifacts/tests/qemu-custom-kernel-verify-15`) and
   Phase B (`qemu-ramboot-custom-full`, out `artifacts/tests/qemu-ramboot-custom-full-6`).
3. Full unittest suite into `artifacts/tests/unittest-ui-1/log.txt`.
4. Camera, RAM only, **on `/dev/stream`**: stock-OS over-USB `--flash-only` and
   `--bridge --stream --vph-kick` (telemetry live), then the full-OS `deadman=90` handover with the
   same binaries into `artifacts/tests/camera-fullos-ui-2`.

## Results (2026-11-23)

- **Build `artifacts/tests/custom-os-build-26`** — Werror clean, GLIBC ≤ 2.11, no VFP/NEON;
  `hybrid_gui 0dff3fdc…`, `imlaunch 42efcd8a…`, `mwftap.so 43f33830…`, `wifi_up.sh 1b1e5663…`;
  initrd `custom-full-initrd.ext2.gz` 1,077,183 B, `sha256 186c4c7f…` (newer than `imlaunch`).
- **Phase A `artifacts/tests/qemu-custom-kernel-verify-16` — PASS** (67.2 s): all eight items
  true, including `cam_ready`, `vph` and `ui`; `[HYBRID_VPH] … ch53_pa=0x23a80000 …
  0x144=01000233`; no fault, no `Exception>`.
- **Phase B `artifacts/tests/qemu-ramboot-custom-full-7/custom_kernel_full` — OK** (441 s,
  `hybrid_ready=True`, `watchdog_quiet=True`, zero guard failures): all eight items true;
  the kicker log (`kicks=4928 repairs=9`, `ch53 ctrl=00000003 addr=23a80000`) is direct evidence
  that something re-pointed the OSD nine times and the descriptor re-assert repaired it.
- **Camera bundle `artifacts/custom-kernel-fullos-loader-v1`** regenerated from that initrd:
  `initrd=0x00700000,0x0010707f` (1,077,183 B), manifest FNV `53de192c`, cmdline `8eee34b3`
  with `custom_os.qemu=1 custom_os.osd=stream`.
- **Camera run — blocked on enumeration**: `camera_debugger --transport extcmd ping` returns
  `Expected one existing MSC/MTP camera; found 0`. Nothing was pushed and no mode switch attempted.
- **Unittests** (`artifacts/tests/unittest-ui-1/log.txt`): 2577 tests, 2 failures, both known local
  ones; no `_ctypes` import errors (the 152 import errors reported by the external review are not
  reproducible in this venv). `knowledge_map check` → OK.

## Remaining step (manual prerequisite)

The only unfinished item is the on-camera confirmation. It requires the ILCE-6400 to be powered on,
awake, set to MSC and attached to a direct host port; then:

1. stock-OS RAM-only: `hybrid_gui --flash-only` (expect a ~2 s magenta panel flash and
   `[HYBRID_SH] flash-only <dev> kicks=<n≥60>` in klog);
2. `launch_custom_kernel_flow.py --mode deadman=90 --bundle-dir artifacts/custom-kernel-fullos-loader-v1`
   (expect the animated UI over LiveView; klog ring must contain `IML cam_ready 1` and
   `[HYBRID_VPH] … ch53 addr=23a80000`; post-reboot health checks).

## Acceptance status

| Check | Status |
|---|---|
| Build + hashes | PASS (`custom-os-build-26/sha256.txt`) |
| QEMU Phase A | PASS (`qemu-custom-kernel-verify-16/result.json`) |
| QEMU Phase B | OK (`qemu-ramboot-custom-full-7/custom_kernel_full/result.json`) |
| Camera bundle identity | PASS (manifest/sha256 cross-checked) |
| Unittests | PASS — `artifacts/tests/unittest-ui-1/log.txt`: 2577 tests, 2 failures, both the known local ones (camera-daemon `a6400-lived` pin size/hash; untracked `Sony-PMCA-RE`); knowledge map `check` OK |
| Camera self-test + handover | BLOCKED — camera not enumerating |

## Acceptance (original criteria)

| Check | Artifact |
|---|---|
| Build | `artifacts/tests/custom-os-build-26/sha256.txt`: Werror clean, GLIBC ≤ 2.11, no VFP/NEON |
| QEMU Phase A | `artifacts/tests/qemu-custom-kernel-verify-16/result.json`: PASS, 168/0, `en144=01000233`, `ch53 addr=23a80000`, ≥2 ch51 bases, non-blank screendump, ≥1 key event, `cam_ready` item true |
| QEMU Phase B | `artifacts/tests/qemu-ramboot-custom-full-7/custom_kernel_full/result.json`: same items, verdict OK |
| Handshake marker | `artifacts/tests/qemu-custom-kernel-verify-16/serial.log` and `artifacts/tests/camera-fullos-ui-2/klog-ring.txt`: `IML cam_ready 1` |
| UI/OSD marker | same files: `[HYBRID_SH] osd=` and `[HYBRID_VPH] … ch53 addr=…` |
| Panel registers | `artifacts/tests/camera-fullos-ui-2/vphreg.txt`: `en144=01000233`, `ch53 addr=23a80000` |
| Camera health | `artifacts/tests/camera-fullos-ui-2/post-reboot-state.txt`: new `boot_id`, `/tmp_bt` without payload, `ramboot` absent, ping answers |
| Unittests | `artifacts/tests/unittest-ui-1/log.txt`: only the two known local failures |
| Panel visibility (manual) | user observation of the magenta self-test and the animated UI, recorded in `artifacts/tests/camera-fullos-ui-2/panel-observation.txt` and labeled as an observation, not host evidence |
