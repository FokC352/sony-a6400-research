# E0717 — Physical camera loader-branch run (`deadman=60`): av-cam oops before the Custom UI

Date: 2026-11-23. **Physical camera run, explicitly authorized** ("test if we can see the liveview and UI
now", camera in MSC, deadman 60 s). Body: the local ILCE-6400 at MSC. RAM-only: the only camera
writes were under `/tmp_bt` (stage tmpfs, mode file, `wifi_up.sh`, `tcp_debugd`, `vphreg`,
`hybrid_gui`). No SD write, no firmware install, no updater/service mode, no driver replacement.

## Question

The offline gates are green on one build (E0712): Phase A `8/8`, `ui: true`, and Phase B
`custom_kernel_full` / `hw_keep_mmc` / `hw_sdio` all OK. Does the same build, booted on the camera
through the documented loader flow, show the stock live view and the Custom UI?

## Inputs

| Input | Identity |
| --- | --- |
| bundle | `artifacts/custom-kernel-fullos-loader-v1`: `custom-Image` `33803be1…26b8`, `RBINITRD.CPIO` `6d34c1bf…073f`, `RBCMDLN.TXT` `cb845ad8…47b1` (`custom_os.qemu=1 custom_os.osd=stream custom_os.debugd=1`) |
| staging tools | `hybrid_gui` `bd5b050b…7c1a` (build-66), `tcp_debugd` `a6891ef6…` , `im_cleanup` `6d0bfa2f…`, `vphreg` `b2f1b7aa…`, `wifi_up.sh` `1b1e5663…` |
| launcher | `custom_os/launcher/launch_custom_kernel_flow.py`, `--mode deadman=60 --skip-liveview-wait` |
| receipts | `artifacts/tests/camera-fullos-ui-3/loader2/result.json`, `klog-ring-deadman60.txt`/`.json` |

## 1. Panel self-test (stock OS, `/dev/stream`)

`hybrid_gui --flash-only` ran from the stock OS through the debugger. klog:
`[HYBRID_SH] flash-only stream kicks=120` — the binary runs on the camera, the `/dev/stream` OSD A/B
map works, and 120 ch53 start-bit re-arms execute at ~60 Hz. The panel self-test path is sound.

## 2. Pre-flight display state (stock OS, read-only `vphreg`)

```
VPH_140_BEFORE=30000000
VPH clk=00000111 en144=01000232 osdbpp=00000000 |
ch51 ctrl=00000002 st=00000000 addr=14e90000 lb=00000400 skip=00000080 lines=000002ab |
ch53 ctrl=00000002 addr=23b16000 lb=00000500 lines=000001df
```

Two observations:

1. **The physical live-view plane is DRAM `0x14E90000`**, not the `0xFE060000`/`0xFE16A400` the guest
   programs inside QEMU. The address is a property of the running guest, not a fixed hardware PA;
   E0712's `0xFE000000` window remains the QEMU-side contract only.
2. The launcher's live-view gate requires the literal `ch51 … st=80000000`; the camera reports
   `st=00000000` while the plane is armed and `0x140 = 0x30000000`. That gate is a **false negative**
   in this state, and the run continued with the documented `--skip-liveview-wait`, justified by the
   `addr=14e90000` / `ctrl=00000002` evidence above.

## 3. Handover

`[RAMBOOT] dryrun: payload verified and staged; no handover.` passed, then
`[RAMBOOT] start: dryrun=0 fault=0x000000 deadman=60s`, staged `RBKERN.IMG 4421888 B` at
`0x00108000`, `RBINITRD.CPIO 1081975 B` at `0x00700000`, and the trampoline committed at
`PA 0x06E7A000`. The TCP debugger dropped at **3.065 s** (the handover took over CPU/RAM).

## 4. Custom OS boot and the failure

The custom OS took the loader branch and started the app:

```
[  808.485472] [CUSTOM_OS_SH] osd_flag=--stream
[  808.581780] [CUSTOM_OS_SH] branch=qemu
[  808.594834] [CUSTOM_OS_SH] path /usr/bin/im.elf ok
[  808.643456] [CUSTOM_OS_SH] imlaunch pid=179
[  808.661165] [CUSTOM_OS_SH] debugd pid=181
[  809.433732] LIRO (liro) rev:4-MUSASHI
[  809.898958] start entry_main(635c6058)
[  809.934795] end entry_main
[  809.975983] ERROR:arch_phys_to_cache:pa=0x3e978600
[  811.468019] Unable to handle kernel NULL pointer dereference at virtual address 00000000
[  811.664266] Internal error: Oops: 817 [#1] PREEMPT SMP
[  812.352267] PC is at 0x63907870
[  813.005338] Process liro-kliro_101 (pid: 275, stack limit = 0x68116128)
[    0.765153] WARMBOOT
```

`arch_phys_to_cache` is called from `0x635dd8eb` (inside the loaded av-cam image at `0x635C6000`,
offset `0x178eb`); the kernel returns NULL for PA `0x3E978600` and av-cam dereferences it. The
kernel's exception monitor then warm-boots (`WARMBOOT`).

**`hybrid_gui` never started.** `custom_start.sh` starts it only after `wait_cam_ready` succeeds
(the flag file imlaunch writes after a clean replay plus VDF unmute), so the Oops ~2 s after
`entry_main` removed both the UI and the live-view handover. No `[HYBRID_SH] bridge_main` or
`[HYBRID_GUI_READY]` line exists in the ring.

## 5. Return and health

On the panel the operator saw **only black** for the whole custom-OS window. That is consistent
with the receipt: the custom kernel re-initialised the display (so the stock frame did not stay),
and because av-cam died before arming ch51/ch53 nothing was composed. It is not a "no handover"
symptom — the handover demonstrably took over.

The stock firmware came back through the warm boot:

- `camera_debugger ping` → `PONG … pid=700 transport=usb_extcmd`;
- `/proc/uptime` `262.84` monotonic; `im.elf` running (pid 149);
- `/tmp_bt` holds only the stock entries (`ble_local_keys`, `bsa_s_pid`, `bt-avk-fifo`,
  `bt-daemon-socket`, `mod_loader_card_boot.once`), no staging leftovers.

## 6. Reproducibility and the cross-run pattern

The same failing call appears in **every** preserved camera ring, including the runs that did show a
live view:

| Run | initrd | `arch_phys_to_cache pa=0x3e978600` | Oops |
| --- | --- | --- | --- |
| `camera-fullos-live-1` | `b9392bb9…` | yes | no |
| `camera-fullos-loader-live-1` | `b9392bb9…` | yes | no |
| `camera-fullos-stream-live-1` | `43f20544…` | yes | no |
| `camera-fullos-ui-2` | `186c4c7f…` | yes | **yes** |
| `camera-fullos-ui-3` | `dcae5b91…` | yes | **yes** |
| `camera-fullos-ui-4` | `dcae5b91…` | yes | **yes** |
| `camera-fullos-ui-5` | `d3c6cd7d…` | yes | no |
| `camera-fullos-ui-6` | `d3c6cd7d…` | yes | no |
| `camera-fullos-ui-8` | `a713ac05…` | yes | no |
| this run | `6d34c1bf…` | yes | **yes** |

The caller is identical (`0x635dd8eb` in the av-cam image, thread `tsk_thread_helper`), and the PA is
identical (`0x3E978600`, inside the stock `memrsv=704M@0x14000000:uc` window). So
`arch_phys_to_cache` returns NULL for that PA in all of them; what differs is whether av-cam then
dereferences the result. The kernel is the same `33803be1…26b8` everywhere, so this is **not** a
kernel regression: it is a state/configuration-dependent av-cam path (the runs differ in initrd, i.e.
in start-script timing and launcher tokens).

`hybrid_gui` never appears in any ring, because it starts only after `cam_ready` and no run has
reached that point on hardware.

## 7. Why the offline gate did not catch this

The QEMU model serves `arch_phys_to_cache` and returns a usable alias for any DRAM PA, so av-cam's
call succeeds there. The failure is a kernel/av-cam interaction on real DRAM allocation layout that
the model does not reproduce. Phase A also starts `hybrid_gui` unconditionally in the QEMU branch
sequence, while the camera path is gated behind `cam_ready`, so the offline run cannot show the
"UI never starts" ordering either.

Two harness issues are visible and independent of the crash:

- the live-view gate's literal `st=80000000` rejects a perfectly active display;
- with `deadman=60`, the camera's ~4x-slower replay (~54 s) leaves almost no UI window even when the
  app does not fault.

## 8. Next boundary

1. **Find the configuration difference** that decides the Oops. The non-crashing runs exist
   (`ui-5`/`ui-6` `d3c6cd7d`, `ui-8` `a713ac05`) but their initrds were overwritten, so the first
   step is to reuse their *launch configuration*: the loader bundle cmdline currently appends
   `custom_os.debugd=1` (a USB debug daemon that starts 0.3 s before av-cam and allocates memory),
   and the `imlaunch … codes=ui ui=1` token set is also a candidate. A single run without
   `custom_os.debugd=1` and with `deadman>=150` is the cheapest discriminating experiment.
2. Decode what av-cam does at `PC 0x63907870` (caller `0x635dd8eb`): whether it uses the NULL alias
   directly or in a branch that depends on replay order.
3. Fix the live-view gate to accept `ch51 ctrl=00000002 addr!=0` regardless of `st`
   (`0x14E90000` measured while the display was active).
4. Only after the Oops is gone, test the UI: `wait_cam_ready` must succeed before the deadman, and
   the camera's replay takes ~54 s, so `deadman>=150`.

Camera state after the run: stock firmware, MSC, responsive; no SD or firmware changes were made.
