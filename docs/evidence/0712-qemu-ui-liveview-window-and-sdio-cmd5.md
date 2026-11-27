# E0712 — QEMU Phase A `ui: true` via the 0xFE000000 live-view window, and the SDIO CMD5 function field

Date: 2026-11-23. Offline only: QEMU `sony-a6400` in WSL, the private Custom OS build tree and the
recorded receipts. No camera, USB, SD, `/dev/mem` or storage access was made in this work.

## Question

The plan `custom_os_kernel_swarm_fix_plan.md` asks for two offline blockers to be closed on one
build before any camera run:

1. **Lane 1** — Phase A (QEMU Custom OS gate) must reach `8/8 PASS` with `ui: true`: the panel must
   show the ch51 live-view bars **and** the ch53 Custom OS UI composed in one frame.
2. **Lane 2** — Phase B `custom_kernel_full_hw_sdio` must pass (`mmcio_mount_failed: false`) while
   the negative `custom_kernel_full_hw_keep_mmc` keeps `mmcio_mount_failed: true`.

The 177-command lens model and the DWC3 post-handover work stay deferred.

## Inputs and identities

| Input | Identity (sha256) |
| --- | --- |
| QEMU | `~/sony-a6400-qemu-11.1.3/build/qemu-system-arm` `1aa614a5…cf34fe` |
| `hw/arm/sony_a6400.c` | `6f576fce…ce30` |
| `hw/misc/cxd90045_cam.c` | `4e474701…114de2` |
| `hw/misc/cxd90014_vph.c` | `516404fe…df4940` |
| `hw/sd/sd.c` | `d2411b99…432f4f` |
| `custom_os/hybrid/hybrid_gui.c` | `8c1c1889…fb99a` (binary `bd5b050b…7c1a`) |
| `custom_os/hybrid/gate_checks.py` | `fe5dbb1c…9f6ce` |
| `custom_os/hybrid/run_qemu_custom_kernel_verify.py` | `8e4e6c90…44d5f3` |
| `custom_os/ramboot/run_handover_test.py` | `d825c45d…b143a2` |
| `tests/test_qemu_liveview.py` | `e72f2df5…a5d0` |
| `tests/test_qemu_vph_composite.py` | `3d3429c9…75ba` |
| `tests/test_qemu_sdio_card.py` | `de211911…fad361` |
| Custom OS initrd | `custom-full-initrd.ext2.gz` `6d34c1bf…073f` (1081975 bytes) |
| kernel / fixture | `custom-Image` (ramboot v1); `replay-min5-nolens-v1.txt` (168 commands) |

Receipts: `artifacts/tests/qemu-custom-kernel-verify-66-final` (Phase A),
`qemu-ramboot-hw-11-cis`, `qemu-ramboot-final-1` (Phase B), `qemu-ramboot-hw-9-trace` and
`qemu-ramboot-hw-10-cccr` (command traces).

## 1. Lane 1 — why the UI gate failed with both layers present

The failure was **not** the OSD ownership problem the plan assumed. The receipt before the fix
(`qemu-custom-kernel-verify-64-handler`) shows the UI in the scanned buffer and the panel showing it:

- `osd_buffers.a/b` `alpha_f_words = 78020` — the Custom UI frame (opaque HUD bands) was in
  `0x23A80000`/`0x23B16000` at the end of the run;
- `panel_after`: `colors 123`, `non_black 80160` — the HUD bands were composed;
- but `roi_after`: `bar_contrast 16`, `bar_levels 2`, `accent_pixels 0`.

The live-view band was uniform, i.e. **ch51 was armed with a zero plane**:

1. `vphreg` in the same receipt: `ch51 ctrl=00000002 addr=fe060000 lb=00000400 skip=00000080
   lines=00000239` and the alternating base `fe16a400`. The camera therefore programs a 1024-wide
   Y8 plane (lb `0x400`, skip `0x80` ⇒ pitch `0x480`, 570 lines ⇒ `0xA0480` bytes) at
   `0xFE060000`/`0xFE16A400`.
2. The AIF live-view writer (`hw/misc/cxd90045_cam.c`, `aif_write_liveview_plane`) accepted only
   the RAM0 heap (`0x04800000..0x40000000`), so every frame was refused:
   `cxd90045-aif: invalid Y8 PA 0xfe060000` / `0xfe16a400` in the QEMU `guest_errors` log.
3. The machine modeled `0xFE000000` as **1 MiB** of SRAM (the CXD90014/a5000 mailbox pattern, C01).
   That window cannot hold `0xFE060000 + 0xA0480` (`0xFE10A480`) nor `0xFE16A400 + 0xA0480`
   (`0xFE20A880`), which is why the writer's address had to be refused.

So the declared `liveview-source=bars` fixture never reached the plane the guest programmed, and the
VPH composed a black live view under a real Custom UI frame. `ui: false` followed from that, not
from av-cam overwriting the OSD.

## 2. Changes

| File | Change |
| --- | --- |
| `hw/arm/sony_a6400.c` | `CXD90045_SRAM_SIZE` 1 MiB → `0x220000` with the measured plane addresses in the comment: the window must back the two Y8 planes the camera programs (`0xFE060000`, `0xFE16A400`). |
| `hw/misc/cxd90045_cam.c` | `aif_write_liveview_plane` accepts `0xFE000000..0xFE220000` in addition to the RAM0 heap; every other address is still refused. |
| `custom_os/hybrid/hybrid_gui.c` | the supervisor child re-arms ch53 when the guest has left the start bit clear for **15 ms** instead of 100 ms (hardware clears ch53's start bit every frame, E0648), so a screendump lands on a composed frame. |
| `custom_os/hybrid/gate_checks.py` | the "before" frame is captured before any key; the rear-center press still proves the key path, and a **closing** press applies and closes the HUD editor, because the pixel ROI samples the live-view band and a modal covers it by design. |
| `tests/test_qemu_liveview.py` | new `test_liveview_plane_writes_into_display_window`: the RC0 AIF writes bars at `0xFE060000` (`235/210/235`) and still refuses `0x01000000`. |

## 3. Lane 1 result — Phase A PASS (8/8, `ui: true`)

`artifacts/tests/qemu-custom-kernel-verify-66-final/result.json` (final QEMU, build-66):
`verdict PASS`, `liro_load/dmmrule5/replay/vdf_unmute/cam_ready/vph/ui/f30a_fill` all `true`,
`elapsed_s 65.8`. The first green run was `qemu-custom-kernel-verify-65-bars` with identical
numbers.

Pixel evidence from the same receipt:

| Item | Before | After keys |
| --- | --- | --- |
| `panel_before/after` | `colors 227`, `non_black 283365` | `colors 254`, `non_black 283343` |
| `bar_means` | `168,168,143,143,105,105,80,80` | `168,167,139,139,102,102,80,80` |
| `bar_contrast` / `bar_levels` | `88` / `4` | `88` / `4` |
| `accent_pixels` (Sony-orange chip) | `1183` | `1183` |
| `osd_buffers.a/b` `alpha_f_words` | — | `11415` / `11415` |
| HDMI-head negative control | — | `ok: true` (ROI rejects it) |

`vph` in the same run: `en144=01000233`, `0x140=10000000`, `ch51 ctrl=00000002 addr=fe16a400
lb=00000400 skip=00000080 lines=00000239`, `ch53 ctrl=00000002 addr=23a80000`. `r140_ok: true`,
`key_events: 3`, `hybrid_gui_ready: true`.

What the gate now proves: the model fetches the ch51 plane from the address the **guest** programmed,
the **guest's** Custom UI frame is composed over it, and the pixel ROI separates the two signatures
(bars contrast ≥ 40 with ≥ 4 levels, orange mode chip ≥ 200 px) while rejecting the HDMI head that
has no composition. The bars are the machine's declared `liveview-source=bars` fixture delivered by
the AIF DMA model, not host-written rendering: `hybrid_gui` never writes the video area in bridge
mode (it leaves it transparent), and no UI pixel comes from the host.

## 4. Lane 2 — SDIO CMD5 function field, CCCR and common CIS

`mmc_drv.ko`'s `mmc_io_detect` rejects an R4 (CMD5) word whose bits 28–30 (the I/O-function field)
are clear: `bics #0xff000000; beq fail` and `tst #0x70000000; beq fail` at `0xee6c`/`0xee74`
(E0412 corrects E0410: the mask is on the CMD5 response, before any CMD52). The model returned
`0x80FF8001`, which has that field clear, so the SDIO path aborted at the filter and fell through to
the SD-memory probe, where the missing ACMD41 cost the measured ~1 s.

### 4.1 CMD5 function field

`hw/sd/sd.c` (`case 5`): `r = 0x90000000 | 0x00ff8000 | 1` — ready bit, 3.2–3.3 V window and a
non-zero function field, with a qtest assertion that all three are set.

Observed effect (`qemu-ramboot-hw-4-r4fix`, `-5`): the post-`isUSH2` wait dropped from `1042.8 ms`
to `314 ms` and `mmcio_mount failed!` still followed, so the rejection had moved deeper.

### 4.2 Command-level trace

The harness gained `--trace-sdhci` (`custom_os/ramboot/run_handover_test.py`), which records
`sdbus_command`, `sdhci_send_command` and `sdhci_response4` into
`<case>/custom_kernel_full_hw_sdio-sdhci-trace.log`. The mount prints no command-level error of its
own, so this is what turns `mmcio_mount failed` into an observed sub-step.

`qemu-ramboot-hw-9-trace` (with the R4 fix only) shows the driver's exact SDIO walk:

```
CMD05 arg 0x00000000      -> R4 0x90ff8001
CMD05 arg 0x00300000      -> R4 0x90ff8001
CMD03                     -> R6 0x00010000
CMD07 arg 0x00010000      -> selected
CMD52 read 0x08,0x13,0x09,0x0A,0x0B,0x14,0x15   (capability, speed, CIS pointer, UHS)
CMD52 read 0x04 (int enable), write 0x04 = 0
CMD52 read 0x07 (bus interface), write 0x07 = 2 (4-bit)
CMD52 read 0x04, write 0x04 = 1
CMD52 read 0x13, write 0x13 = 0
CMD52 read 0x00 (CCCR/SDIO revision = 0)
```

Every one of those reads returned `0`, i.e. the model reported an invalid card: `0x00` is the
reserved CCCR/SDIO-revision encoding, and the read-backs of `0x04`/`0x07` never matched the value
the driver had just written. So the failure was **inside the CCCR walk**, not in the CMD5 filter.

### 4.3 CCCR and common CIS

`sony-sdio-wifi` now implements the function-0 register space the driver touches, with
specification values rather than a guessed chip image:

| Register | Value | Basis |
| --- | --- | --- |
| CCCR 0x00 | `0x02` | CCCR 2.00 / SDIO 2.00; `0x00` is the reserved encoding no card reports |
| CCCR 0x01 | `0x02` | SD physical spec 1.10 |
| CCCR 0x09–0x0B | `0x001000` | common CIS pointer |
| CCCR 0x04/0x07/0x13 | read-back | writes are stored so the driver's read-back matches |
| CIS at 0x1000 | MANFID `(0x20,4)`, FUNCID `(0x21,2)`, FUNCE `(0x22,4)`, END `0xFF` | minimal valid tuple stream |

The camera's Wi-Fi vendor/product id is not captured, so the CIS uses Broadcom's registered SDIO
vendor id `0x02D0` with the **declared placeholder** product id `0x0100`, and this limitation is in
the code comment and in this record. The mmcio layer only needs the stream to parse; a later layer
that keys off the product id is a separate boundary.

Observed effect: `qemu-ramboot-hw-10-cccr` (CCCR only) advanced past the revision check and then
read the common CIS at `0x00200000` (address `0x1000`), which the model answered with the R5
command-error flag — the exact next failure. `qemu-ramboot-hw-11-cis` (CCCR + CIS) produced **no
`mmcio_mount failed!` line at all**.

### 4.4 Lane 2 result

`artifacts/tests/qemu-ramboot-hw-11-cis/summary.json`:

```json
{"case": "custom_kernel_full_hw_sdio", "verdict": "OK",
 "hw_markers": {"exception": false, "mmcio_mount_failed": false,
                "wifi_done": false, "environment_hardware": true}}
```

`mmcio_mount_failed: false` with `environment_hardware: true` is the plan's positive condition.
`wifi_done` stays false: the DHD layer still logs `no wifi platform data, skip`, which is a separate
boundary (the platform-data table, not the SDIO mount) and is not part of this plan's target.

## 5. Final same-build gate

All of these ran on one tree state: kernel `33803be1…26b8`, initrd
`6d34c1bf…073f` (1081975 bytes), QEMU `1aa614a5…cf34fe`, `ramboot-build-11`:

| Case | Receipt | Verdict |
| --- | --- | --- |
| Phase A Custom OS | `qemu-custom-kernel-verify-66-final` | `PASS`, 8/8, `ui: true` |
| Phase B loader | `qemu-ramboot-final-1/custom_kernel_full` | `OK`, `hybrid_ready: true`, gate `ui: true` |
| Phase B hardware negative | `qemu-ramboot-final-1/custom_kernel_full_hw_keep_mmc` | `OK`, `mmcio_mount_failed: true`, `environment_hardware: true` |
| Phase B hardware positive | `qemu-ramboot-hw-12-samebuild/custom_kernel_full_hw_sdio` | `OK`, `mmcio_mount_failed: false`, `environment_hardware: true` |

Each receipt's `custom-full-payload.json` records the same `RBINITRD.IMG` hash
(`6d34c1bf…073f`) and `RBKERN.IMG` hash (`33803be1…26b8`), which is what makes this a single-build
gate. `qemu-ramboot-hw-11-cis` is the earlier positive run on the previous initrd
(`d21d0a62…c1106`); it produced the same markers and is superseded by `hw-12-samebuild`.

Full regression: `python -m unittest discover -s tests -q` → 2638 tests, 2 failures, both
pre-existing and unrelated (the `camera-daemon` binary pin and the `Sony-PMCA-RE` directory
presence); `knowledge_map check` OK after regenerating `docs/images/research-status.svg`.

The plan's final Lane 3 step is the camera bundle, and it was regenerated from this green build only:

```
python scratch/make_fullos_loader_bundle.py
artifacts/custom-kernel-fullos-loader-v1/
  custom-Image  sha256 33803be1…26b8 (4421888 bytes)
  RBINITRD.CPIO sha256 6d34c1bf…073f (1081975 bytes)
  RBCMDLN.TXT   1738 bytes, qemu_token=True
```

`RBINITRD.CPIO` is the same `custom-full-initrd.ext2.gz` that produced the green Phase A and
Phase B receipts; the loader-branch cmdline carries `custom_os.qemu=1 custom_os.osd=stream
custom_os.debugd=1`. No camera contact was made.

## 6. Reproduce

```bash
cp -R qemu-a6400/qemu-overlay/. ~/sony-a6400-qemu-11.1.3/
cd ~/sony-a6400-qemu-11.1.3/build && ninja qemu-system-arm
cd "/mnt/c/Users/Administrator/Desktop/a6400 project"

python3 -m unittest tests.test_qemu_vph_composite tests.test_qemu_sdio_card \
    tests.test_qemu_sdio_warm tests.test_gate_checks tests.test_qemu_liveview -q

bash custom_os/launcher/build_wsl.sh artifacts/tests/custom-os-build-66
python3 custom_os/hybrid/build_full_initrd.py --build-dir artifacts/tests/custom-os-build-66
python3 custom_os/hybrid/run_qemu_custom_kernel_verify.py \
    --build-dir artifacts/tests/custom-os-build-66 \
    --out artifacts/tests/qemu-custom-kernel-verify-66-final

python3 custom_os/ramboot/run_handover_test.py --build artifacts/tests/ramboot-build-11 \
    --out artifacts/tests/qemu-ramboot-final-1 \
    --case custom_kernel_full --case custom_kernel_full_hw_keep_mmc --boot-wait 30
python3 custom_os/ramboot/run_handover_test.py --build artifacts/tests/ramboot-build-11 \
    --out artifacts/tests/qemu-ramboot-hw-12-samebuild \
    --case custom_kernel_full_hw_sdio --boot-wait 30
# the same command with --trace-sdhci writes <case>/<case>-sdhci-trace.log
```

## 7. Next boundary

Lane 1 and Lane 2 are closed for the offline gate. What remains outside this plan's target:

1. The DHD layer still prints `no wifi platform data, skip` and retries power-up; `wifi_done`
   stays false. That is the platform-data table for the Broadcom adapter, not the SDIO mount.
2. The common CIS declares a placeholder product id (`0x0100`) because the camera's Wi-Fi
   vendor/product id was never captured; if a later layer keys off it, a captured card dump is
   needed rather than a guessed value.
3. The OSD ownership contract (F03) is still in progress: the UI survives because it copies its
   frame into A/B on the ch53 arm edge, not because av-cam's OSD engine is muted. Closing it needs
   the app-side VDF owner (the type-8 dataflow packet from E0711).
4. Only after the phase-A and phase-B gates above are green on one build may a camera bundle be
   regenerated (the plan's manual step, `deadman=120`); this work made no camera contact.
