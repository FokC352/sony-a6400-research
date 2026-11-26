# E0688 — QEMU VPH panel composition contract, pixel-level gate and the Custom OS UI buffer boundary

Date: 2026-11-23. Offline only: QEMU `sony-a6400` in WSL, the private Custom OS build tree and
the recorded camera receipts. No camera, USB, SD or `/dev/mem` access was made in this work.

## Question

The plan `qemu_digital_twin_swarm_plan.md` (Paket A/C) asks the QEMU VPH model to reproduce the
camera's panel behaviour instead of a permissive approximation:

1. what do VPH `+0x140` (the camera reads `0x30000000`), `+0x144` (`0x01000232` on the camera,
   `0x01000233` in the older QEMU gate) and `+0x404` actually gate, and can the guest special-case
   `custom_os.qemu=1` be removed?
2. does the QEMU gate prove that the ch51 LiveView layer **and** the ch53 Custom OS UI are
   composed in the same frame, and does it fail when they are not?
3. the plan's negative test — "QEMU must hide the UI while `+0x140 = 0x30000000`" — is it correct?

## Inputs and identities

| Input | Identity |
| --- | --- |
| QEMU | `~/sony-a6400-qemu-11.1.3/build/qemu-system-arm`, sha256 `299c538e…a19b` (overlay synced from `qemu-a6400/qemu-overlay/`) |
| VPH model | `qemu-a6400/qemu-overlay/hw/misc/cxd90014_vph.c`, sha256 `3e5a4ace…d8a5` |
| gate | `custom_os/hybrid/gate_checks.py` sha256 `642379f1…48f6`, `run_qemu_custom_kernel_verify.py` |
| tests | `tests/test_qemu_vph_composite.py` sha256 `8b6d1b47…adb0`, `tests/test_gate_checks.py` |
| Custom OS build | `artifacts/tests/custom-os-build-34` (`hybrid_gui` `86fe92f8…e12b`), initrd `37c62f58…808f` in verify-26 (the ext2 image embeds build timestamps, so a rebuild changes the hash) |
| kernel / fixture | `custom-Image` `33803be1…26b8`; `replay-min5-nolens-v1.txt` (168 commands) |
| receipts | `artifacts/tests/qemu-custom-kernel-verify-23`, `-24`, `-26` |

## 1. Register contract and model changes [C/O/I]

Static analysis of the captured a6400 `av-cam.bin` (`90d515cf…9125`, ORIL v4, base `0x635C6000`)
found the AIF channel table (VA `0x63E237F8`: `0xF3441200`, `0xF3441300`, stride `0x80`, eight
channels) and read-modify-write accessors for `+0x00`, `+0x04` and `+0x48`, which grounds the AIF
layout. It found **no literal or `movw`/`movt` construction** of `0xF3440140`, `0xF3440144`,
`0xF3440404` or `0xF3441204` in av-cam, the kernel Image, `im.elf` or any `.ko` [O]. A
table-base-plus-offset access cannot be excluded by that scan, so the register *semantics* below
rest on camera observations and the CXD90014 reference, not on decoded a6400 instructions.

| Register | Evidence | Model |
| --- | --- | --- |
| `+0x144` bit0 | documented panel YUV enable (`voxivoid/a6000-qemu` `RE_DISPLAY_SCANOUT.md` @ `3a039c5`); E0652 records the guest clearing bits 0/1 to `0x3000230` when it blanks the panel | YUV layer when set |
| `+0x144` bit1 | documented/`[?]` OSD layer enable in the same reference; consistent with E0652's blanking write | OSD layer when set |
| `+0x140` bits 28-29 | camera reads `0x30000000` while the panel scans ch53 (`camera-fullos-ui-8` klog ring, E0685); `hybrid_gui` executed `0x140 &= ~0x30000000` and the next read was still `0x30000000` [O] | write-1-to-set, non-clearing; mode 3 also enables the YUV layer |
| `+0x404` bit0 | documented OSD 4 B/px | unchanged |
| AIF `+0x00` bit0 | documented start/busy, cleared at frame end (E0639) | arm latch per frame |

Changes in `cxd90014_vph.c`:

- `+0x140` writes preserve bits 28-31 (`write-1-to-set`); a guest clear is accepted but does not
  take effect, matching the camera. This is marked an inference in the source.
- composition takes `+0x144` bit1 as the OSD enable and `+0x144` bit0 **or** the `+0x140`
  panel-stage mode as the YUV enable. The second term is required because the camera shows live
  view with `0x144 = 0x01000232` (bit0 clear) and `0x140 = 0x30000000`.
- a channel armed between two vsyncs composes the frame being scanned, so a screendump right after
  an arm is deterministic; the per-frame contract is unchanged (a guest that stops re-arming loses
  the OSD at the next vsync).

`hybrid_gui.c` no longer reads `/proc/cmdline` to force `+0x144` bit0: it writes the camera's
`0x01000232` value on every platform and leaves `+0x140` alone.

Five qtests (`tests/test_qemu_vph_composite.py`) drive both AIF channels through qtest and read the
composed panel back from a QMP `screendump`; a two-instruction ARM stub at the board's kernel entry
re-arms ch53 like the guest supervisor. All pass:

| Test | Observation |
| --- | --- |
| camera values (`0x140=0x30000000`, `0x144=0x01000232`) | red OSD rows **and** eight distinct live-view bar levels in one frame |
| `+0x140=0`, bit0 clear | OSD present, middle band black: the YUV layer is not painted unconditionally |
| `+0x144=0x01000230` | OSD hidden, bars visible: bit1 really gates the OSD |
| single arm, then no re-arm | OSD disappears after the frame, bars stay |
| `+0x140` writes | `0x30000000` survives a clear; a later write can only add bits |

Note: qtest's `write` command parses the hex payload from `words[3][2*i+2]`, i.e. it expects an
`0x` prefix. Without it every byte is nibble-shifted; that cost one debugging cycle and is now
documented in the test helper.

## 2. The plan's `+0x140` mute premise is refuted [O]

The camera reads `0x140 = 0x30000000` while its own OSD is on screen (`camera-fullos-ui-8`), and
the custom OS also runs with that value. A QEMU that hid the UI for `0x30000000` would reproduce a
mute that the hardware does not have. The requested `--expect-muted-osd-hidden` gate was therefore
**not** implemented; the negative contracts above (YUV gated by `+0x144` bit0/panel mode, OSD gated
by `+0x144` bit1, OSD lost without re-arm) are evidence-backed instead.

## 3. Pixel-level gate and the real UI boundary [O]

`gate_checks.vph_item` now expects the camera value `en144=01000232` (still accepting the older
`01000233`), records `+0x140` and rejects `0xC…`. `ui_item` adds a region check on the QEMU
screendumps: eight column means in the video band (bar contrast and levels) and the Sony-orange
mode chip in the top-left HUD must both be present. A negative control dumps the HDMI head and
requires the same code to reject it. `tests/test_gate_checks.py` (8 tests) covers the marker
acceptance and the ROI on constructed PNGs.

The first gate run with the new ROI (`qemu-custom-kernel-verify-23`, 119.2 s) returned
`verdict FAIL`, item `ui: false`, while `vph` passed with `en144=01000233` and
`ch53 addr=23a80000`. The panel image contained only a dim orange block and a nearly uniform
middle band (`bar_contrast 16`, `accent_pixels 0`).

`qemu-custom-kernel-verify-24` added an OSD-buffer dump (`pmemsave` at `0x23A80000` and
`0x23B16000`, 0x96000 bytes each). Both buffers were a uniform `0x000D` RGBA4444 word (black,
alpha 13) for all 307,200 words — the camera's own OSD background, not the Custom UI. So the panel
was showing av-cam's OSD, and the previous gate had passed item 6 on that stock surface.

Cause: av-cam/VDF and the Custom UI both draw into the same two panel OSD buffers (E0298: OSD A
`0x23A80000`, B `0x23B16000`). av-cam redraws its OSD after the UI's frame, so the panel scans the
stock OSD. `hybrid_gui`'s dirty-row tracking then never repaints the unchanged rows. A supervisor
child-side copy was tried first and produced a guest page-translation fault at the OSD address
(`hybrid_gui` pid 418, `0x0436b000`), because the `/dev/mem` PFNMAP mapping is not inherited across
`fork()`; that approach was reverted.

The retained change is a parent-side ownership check: before each frame the UI compares one pixel
it owns against the shared framebuffer and forces a full repaint when the buffer no longer carries
its frame. With that build (`custom-os-build-34`) the buffers now contain 78,020 opaque
(`alpha = 0xF`) words, i.e. the UI's frame is written, but the panel still scanned av-cam's OSD at
the sample instant (`qemu-custom-kernel-verify-26`, 89.4 s, `ui: false`, same ROI values). The UI
loses the write race deterministically, which is the same outcome the physical camera showed in
E0685 §2-3 ("live view, no Custom OS UI").

This is a fidelity improvement, not a regression: the QEMU gate now fails exactly where the camera
failed instead of passing on a stock surface. It also confirms E0685's open item — the replay's
OSD surface is not owned by the Custom UI. The final baseline after the ownership experiments is
`qemu-custom-kernel-verify-38` (build `custom-os-build-44`, 93.4 s): same `ui: false`, same
`bar_contrast 16`, same `0x000D` buffer, i.e. the camera's stock OSD and no Custom UI.

## 4. Paket B boundary: the 177-command replay fails inside im.elf, not in QEMU [O]

The no-lens fixture removes exactly nine lens-drive lines (`I S o21 3100 651e` ×2,
`I A o21 3100 7578` ×4, `I A o21 3100 768c` ×3). Rebuilding the initrd with the full captured
`replay-min5-v2.txt` and running the gate (`qemu-custom-kernel-verify-27-full-replay`, 68.8 s)
gives:

```
IML replay done: 177 commands, 6 problems
IML replay  271 IS o21 3100/651e = 0xffff
IML replay  361 IA o21 3100/7578 = 0xffff
IML replay  367 IA o21 3100/7578 = 0xffff
IML replay  369 IA o21 3100/768c = 0xffff
IML replay  381 IA o21 3100/7578 = 0xffff
IML replay  383 IA o21 3100/768c = 0xffff
```

Every failure is a `0xffff` return from `MWF::ObjIf::IssueCommand*` on the guest's lens object
(`o21`), so the missing piece is the lens object's own state machine (it needs the sub-micro/lens
reply that the board cannot produce without a lens-attached capture), not a VPH register. A QEMU
`lens-sim=on` cannot honestly make these succeed from the current captures; the 168-command
no-lens fixture stays the qualified gate.

## 5. Phase B (`custom_kernel_full`) and the Paket B design lanes

### Phase B run

`python3 custom_os/ramboot/run_handover_test.py --build artifacts/tests/ramboot-build-10 \
  --out artifacts/tests/qemu-ramboot-custom-full-1 --case custom_kernel_full`

ran the RAM-only handover into the full Custom OS. Receipt: `hybrid_ready: true` at 319.5 s, all
gate items `ok` except `ui` (the same pixel ROI failure as Phase A), so the verdict is
`UNEXPECTED` only because of the UI item. The post-handover VPH dump differs from Phase A:
`ch51 addr=fe16a400` (SRAM) vs `14e90000`, `VPH_140_BEFORE=10000000` (av-cam sets bit 28 only, not
`0x30000000`), `en144=01000233`. Phase B therefore reproduces the UI boundary, not a new fault.

### E0410 warm SDIO/MMC (`mmc1`)

Decoded from the `udif_devices` records: `mmc1` is `0xF0301000`, GIC IRQ 221 (`E0650`). QEMU maps
only `mmc0` (slot, `0xF0300000`, IRQ 218) and `emmc0` (`0xF0304000`, IRQ 227); `0xF0301000` is
unmapped, so no IRQ 221 can assert. `ramboot.ko`'s `quiesce()` stops the watchdogs, DWC3, VPH/AIF,
work DMA and PL330 but touches no MMC register, and the jump resets nothing, so SDHCI state, the
card's power/RCA and GIC pending bits survive. The camera's failing path is card enumeration
(`CMD5`/`mmc_io_detect`) after the host power stages succeed (`E0410`), so the minimal faithful
model is a real SDHCI block at `0xF0301000` with **no card**: CMD5 times out, `CMDTIMEOUT|NIS_ERR`
asserts IRQ 221, which is the E0410 symptom rather than an injected fault. A positive test needs a
small SDIO card model (QEMU's `sd.c` has no SDIO) answering CMD5/CMD3/CMD7/CMD52 behind an opt-in
`sdio-wifi=on` property. `ramboot.ko` would quiesce the host by raw MMIO (no exported MMC API):
poll `+0x2c` inhibits clear, `+0x34/+0x36=0`, `+0x30=~0` (W1C), clock off `+0x28`, power `+0x29=0`,
SW reset `+0x2f=1`, then clear GIC pending/enable for SPI 221. The camera's real `mmc1` register
values and the PC-0 mechanism (stale GIC pending vs. an error IRQ into a half-built handler) are
UNKNOWN offline.

### Post-handover DWC3

The controller side is already qualified: stock gadget start (soft reset, event ring, DCTL.RUN,
DEVTEN `0x1E1F`, six EP0 transfers to `configured`) passes in QEMU (`E0597`, `E0598`). The
custom-OS silence is **not** a missing model connection: the loader branch runs
`imlaunch … skip=libUsbControl.so,libHotplug.so` and never starts `im.elf`, so nothing issues
`StartGadget`/`SET_MODE`; `usbg_storage.ko`/`usbg_stillimage.ko`/`usbg_buspower.ko` are not in the
initrd's module list; and the gate never enables `dwc3-gadget`. The plan's "connect the DWC3 model"
assumption is therefore wrong. A useful offline test is a direct custom-kernel cold boot with
`dwc3-gadget=on`, `usb-vbus-test=on`, `usb-cid-high=on` and the captured-hwparams/strict-ep0
globals, asserting either `debug registered` with no `IN` transfer (userspace never started the
gadget) or the E0598 token ledger. The daemon's MSC/extcmd transport (bulk CBW/CSW + vendor request
`0x7A`) is not modelled, so a PONG cannot be expected even with a perfect gadget.

### UI ownership attempts and the narrowed boundary

Four attempts were measured, each with a gate receipt:

1. **Arm-edge signal** (`hybrid_gui` supervisor → `SIGUSR1` → parent repaint). The supervisor now
   watches `ch53 +0x00` and signals on an arm edge; it kicks ch53 itself only when av-cam has not
   armed for 100 ms. Receipt `verify-28`: `kicks=511 arm_signals=51` over 16.6 s, i.e. av-cam arms
   about 3×/s and the supervisor keeps the layer armed otherwise. The UI still does not appear.
2. **Pagemap identity** (`verify-29/33/34`): the UI's `/dev/mem` mapping is the scanned page
   (`va=0x0428a000 pa=0x23a80000 present=1`). An alias probe that writes `0xABCD` into the shm
   framebuffer and reads the OSD mapping back returns the OSD's own value, and the shm page is a
   different frame (`fbpa=0x007f0000`), so **shm and OSD do not alias**.
3. **The UI's own frame changes**: at t=14.6 s `fb(20,20)=0xf82f` (the UI's opaque chip) and
   `fb(320,240)=0x0000` (transparent video area); at t=15.6 s the same pixels read `0x631f` and
   `0x111f`, the exact words of av-cam's OSD, while `forced=0` (the sentinel never saw a
   difference). So the framebuffer the UI renders into ends up carrying av-cam's OSD content.
4. **Reservations and relocation**: adding `memrsv=2M@0x23A80000:uc` to the custom cmdline (the
   stock cmdline reserves the media range with `memrsv=704M@0x14000000:uc`) hangs the early boot
   (no serial output, `verify-36-memrsv`). Allocating the UI framebuffer outside the OSD range with
   a pagemap-verified `mmap` gives a black panel (both OSD buffers all-zero, `verify-37`), less
   faithful than the camera's stock OSD. Both were reverted.
5. **UI-owned ch53 base**: the supervisor was moved to force ch53 to `0x23C00000` (after the A/B
   pair) and the UI mapped that page (`verify-48-uiosd`). `ch53_addr=23c00000` was recorded, but the
   buffer at `0x23C00000` then held av-cam's `0x000D` surface (`alpha_f_words 78007`, same first
   words as A) — av-cam **follows the scanned ch53 base** and draws its OSD there. Relocation cannot
   establish ownership; it was reverted to the stock A/B base.
6. **App context**: `/usr/bin/im.elf` and `/usr/lib/libObj.so` exist in the Custom OS root
   (`verify-51-appctx` path probe). Starting `im.elf debug s g r u l i a lazy-global` from the gate
   shell succeeds (`IMELF_437`), but the follow-up `imlaunch mode=osd_off` helper still faults:
   `page translation fault (0x017) at 0x00000008`, current task
   `/tmp_bt/imlaunch(grm_dn_vdf_if)` pid 324 (`verify-55-appctx3`). The VDF/dataflow call must run
   inside the app's own thread, not a separate process.

The retained state is the arm-edge supervisor and the four-pixel ownership check; the gate stays
red on `ui`, which is the honest result. The remaining hypothesis is that the framebuffer pages
and the media range overlap only for some pages (page 0 does not), so a whole-buffer pagemap
check plus a kernel-side reservation is the next step, not a UI-only change.

### Whole-page scan: the framebuffer does not overlap the OSD range [O]

`scan_pages()` in `hybrid_gui` walks every 4 KiB page of a buffer through `/proc/self/pagemap` and
counts the pages inside `0x23A80000..0x23C00000`. Receipt `qemu-custom-kernel-verify-39-pagescan`
(build-45):

```
[HYBRID_PG] fb  pages=150 present=150 lo=02063000 hi=026d0000 osd_overlap=0 first=00000000
[HYBRID_PG] bb  pages=401 present=401 lo=02000b0c hi=027ffb0c osd_overlap=0 first=00000000
[HYBRID_PG] osd pages=150 present=150 lo=23a80000 hi=23b15000 osd_overlap=150 first=23a80000
```

So neither the shm framebuffer nor the back buffer shares a page with the panel OSD range; the
partial-overlap hypothesis and the plan's `memrsv` remedy are **refuted**. The kernel's allocated
pages sit around `0x02000000..0x02800000`, while av-cam's OSD is at `0x23A80000`, outside the
kernel's managed RAM. A media reservation would change nothing and the partial token already hung
the boot, so it was not applied.

The remaining cause is therefore **guest-side**: av-cam redraws (and per E0652 can zero-fill) the
ch53 buffer after the UI writes it, so the panel scans the stock OSD. The next step is to make the
Custom OS stop or redirect av-cam's OSD draw (replay OSD-close command or a UI-owned ch53 base),
not a QEMU or kernel-memory change.

### Paket B2 implemented: `mmc1` controller and the E0410 negative contract [O]

`sony_a6400.c` now maps a real SDHCI controller at `0xF0301000` with GIC IRQ 221 (`pic[221-32]`),
`sd-spec-version=3`, `capareg=0x016830b0`, and **no card** by default. The stock OS never opens
slot 1 (E0412), so the stock path is unchanged; the custom-OS hardware branch enumerates it and its
CMD5 has no responder, which is the camera's E0410 `mmcio: mmcio_mount failed` condition.

`tests/test_qemu_sdio_warm.py` drives the same sequence through qtest (3 tests, all pass):

| Test | Observation |
| --- | --- |
| controller mapped, no card | `PRESENT_STATE = 0x01FA0000`, card-present bit clear; the unmodelled sibling `0xF0302000` still reads 0 |
| CMD5 without a card | `INTERRUPT_STATUS = 0x8001` (`NIS_ERR` + `CMDTIMEOUT`), `errintsts = 0x1`, and the GIC pending word `0xF8001218` has bit 29 (ID 221) set |
| unmodelled sibling | no status, no IRQ 221 |

Two controller details the test pinned down: the command is generated by a write covering the
upper byte of `CMDREG` (the 32-bit `TRNMOD/CMDREG` pair) with the SD clock enabled
(`CLKCON=0x7`), and the error enables live in the high half of `0x34`/`0x38` (the timeout path
needs both). The GIC pending word is indexed by the hardware ID, so IRQ 221 is word 6 bit 29, not
word 5.

The positive half (a card answering CMD5) is now implemented: the overlay's `hw/sd/sd.c` registers
`sony-sdio-wifi` behind `sdio-wifi=on`, a minimal `TYPE_SD_CARD` subclass that answers CMD5 (R4:
ready | 3.2-3.3 V | one function), CMD3 (R6 RCA), CMD7 (select), CMD8 (R7) and CMD52 (R5
`flags 0, data 0`). `tests/test_qemu_sdio_card.py` passes 3/3 (card present, CMD5 ready + 1
function, CMD3/CMD7/CMD52 with no error), while the default board still times out as above. The
BCM4339's exact OCR/CCCR values are not captured; the responses are the conservative defaults.

`ramboot.c` now also quiesces mmc1 before the jump (token `keep_mmc` skips it for the negative
test): wait for the CMD/DATA inhibit to clear, disable the normal/error interrupt enables, W1C all
status, stop the SD clock, power the bus off, software-reset the host, then clear pending and
disable SPI 221 in the GIC distributor. `artifacts/tests/ramboot-build-11/ramboot.ko` builds with
the token (`keep_mmc`) and the `0xF030` base in the disassembly. The handover-level
negative/positive cases still need a hardware-cmdline case (the harness always adds
`custom_os.qemu=1`); the register sequence itself is checked by
`test_ramboot_quiesce_sequence_clears_host_and_gic`, which replays it against the model and
asserts status 0, reset complete, power off, IRQ 221 not pending and SPI 221 disabled.

Regression evidence: a 90 s stock boot with the new controller (`ULOGIO= A6400_QEMU_DATA_DIR=…
TIMEOUT=90 bash qemu-a6400/run.sh`, 2016 log lines) still reaches `end entry_main` and
`===== DMP_2D_Initialize : 0`, with no `mmc1` error; the Phase A gate still passes every item except
`ui` (`qemu-custom-kernel-verify-40-mmc1`).

### The stock OSD-mute contract (derived from the binaries) [O]

The Custom UI cannot win a per-frame write race against av-cam, so the correct fix is to mute
av-cam's OSD. The stock code has that path, and it is a simple 1/0 flag:

- `dataflowInfraFramework.so` exports `C_DataflowInfra_Entry_OsdMute` (`C1Ev` at `0x248b0`) whose
  4-byte payload lives at `+0x14` (ctor: vtable, `+4=3` entry type, `+0xC` points at `+0x14`,
  `+0x10=4`, payload zeroed). `SetData_All` at `0x248f0` is a 4-byte `memcpy` into `+0x14`; the
  getter at `0x24900` copies it back. `C_DataflowInfra_StdData_OSD::SetEntry_OsdMute` is at
  `0x23078` (12 bytes, stores the entry into the StdData_OSD handle).
- `libObj.so` has the caller: `0x71168c` builds a `StdData_OSD` on the stack from its VDF port
  parameters, constructs the `Entry_OsdMute`, and writes the payload from its second argument:
  `cmp r4,#1; strbeq r4,[sp+0x3c]; strbne r5(=0),[sp+0x3c]` — i.e. **1 = mute, 0 = unmute** —
  then `SetData_All` and `SetEntry_OsdMute`, asserting on failure.
- Its only caller is `0x70b5b6`, which calls it on the object at `this+0xC4` with the flag from a
  message field (`[msg+8]`): the mute is a **message** handled by the app's VDF/OSD owner object.
- The dispatcher is `libObj.so` `0x70b554`: it reads the message type byte (`ldrb r1,[r1]`),
  subtracts 1, bounds it to 0..9 and jumps through a `tbb` table at `0x70b56c`
  (`05 07 09 0f 1f 16 29 21 40 2b`). The entry for type **8** (`r3=7`, table byte `0x21`) lands at
  `0x70b5ae`, sets `r5 = this+0xC4`, loads `r1 = [msg+8]` and calls the mute at `0x71168c`.

So the OSD-mute packet is: **type byte 8, payload byte at `+8` = 1 to mute (0 to unmute)**, applied
to the OSD `StdData` object at `this+0xC4`. The dispatcher's only caller is `0x7146fa`, which passes
`this = r4+8` (a sub-object of the app/port context) and the packet in `r7`; the packet fields at
`+0x400`/`+0x404` select the sub-path for the other message types.

There is also a direct VDF API for the same effect: `OBJAVBBCBASECLASS::VdfCh::setOSDOnOff`
(`libBizFw.so` `0x39198`, param ctor `0x36e0c`) builds a 20-byte VDF message (`+2 = 0x0302`,
`+4 = 20`) from the param fields `+8`, `+9`, `+12` and `+16` and sends it through
`DataflowInfraCh::sndRcvAsyncDataflowMsg`. `imlaunch` already uses the same channel object for
`setYUVMute`/`setPanelOut`, so an opt-in `osd_off=1` was added that calls `setOSDOnOff` with
`+8 = 7` (panel|evf|line) and `+12 = 0`, in a bounded child process.

Measured result (`qemu-custom-kernel-verify-43-osdoff-bounded`, build-52): the symbols resolve
(`IML osd_off symbols ctor=1 prm=1 set=1`), the call **blocks** (`child timeout; killing` then
`child did not exit; leaving it` even after SIGKILL), and the gate proceeds because the parent
never blocks (`IML vdf_unmute setYUVMute=0 setPanelOut=0 osd_off=1`). So the VDF OSD dataflow
service has no app-side owner in the loader branch and cannot complete the mute.

Starting that owner from the loader was then tried: `libObj.so` exports `vdf_if_port_init`
(no arguments; it allocates the dynamic ids 50/368, creating the receiver thread and port object,
and asserts on a second call). Calling it in the bounded child before the mute faults the guest
into Sony's exception monitor (`qemu-custom-kernel-verify-45-portinit`), so the loader cannot
bootstrap the app-side VDF/OSD owner either; it needs the app's startup context (`im.elf`). The
token is kept only as an opt-in diagnostic and removed from the default initrd.

### Hardware-cmdline handover cases (`qemu-ramboot-hw-3`, ramboot-build-11)

`custom_os/ramboot/run_handover_test.py` now has a `hw_cmdline` mode that omits
`custom_os.qemu=1`, plus `hw_branch`/`hw_expect` verdicts over the hardware branch's markers
(`environment=hardware`, `Entering exception monitor`, `mmcio_mount failed`, `WIFI_DONE`):

| Case | Verdict | Markers |
| --- | --- | --- |
| `custom_kernel_full_hw_keep_mmc` (negative, `keep_mmc`) | **OK** | `exception=false`, `mmcio_mount_failed=true`, `environment_hardware=true` (259.4 s) |
| `custom_kernel_full_hw_sdio` (quiesce + `sdio-wifi=on`) | **UNEXPECTED** | same markers: `mmcio_mount_failed=true`, `wifi_done=false` (252.6 s) |

The negative reproduces the E0410 mount failure on the hardware cmdline. The positive still fails
at `mmcio_mount failed` even with the card present: the minimal card answers CMD5/CMD3/CMD7/CMD52,
but the driver's `mmc_io_detect`/mount needs more of the SDIO protocol (card-detect/CCCR/CMD53)
than the current captures provide. In QEMU the camera's warm-state exception itself never appears
(neither case reaches the exception monitor), because the `irq/221-mmc1` PC-0 fault depends on
hardware state the model does not reproduce; both cases show the mount failure, so the quiesce's
effect on the exception cannot be separated offline.

## Failures and limits

- The positive gate is **not** green: `ui` fails because the Custom OS UI is not visible on the
  QEMU panel. Per the plan's stop criterion the camera is not touched.
- The fix direction is buffer ownership: either the Custom UI must write after av-cam's per-frame
  OSD draw (a vsync/arm-edge hook that the child can trigger without touching the PFNMAP mapping),
  or ch53 must be pointed at a buffer av-cam does not redraw (a register the hardware accepts but
  that departs from the captured `0x23a80000` contract). Neither was implemented here.
- `+0x140`/`+0x144`/`+0x404` semantics remain observation-based; the static scan cannot exclude
  table-base-plus-offset accesses.
- The lens-protocol (177 commands), warm SDIO `E0410` model and post-handover DWC3 channel of
  Paket B were not implemented in this change; the 177-command run above shows the lens failures
  are guest-side `0xffff` returns and need a lens-attached capture.

## Deviations from the plan

- `--expect-muted-osd-hidden` was not implemented: the premise is refuted (§2). The negative
  contracts are the qtests instead.
- `custom_os/` stays in `.gitignore` (it is a nested Git repository; un-ignoring it is a
  repository-layout decision for the user, not a worker edit).
- The 546 root `tmp*` directories/files could not be deleted (the shell policy refuses recursive
  deletes outside build/cache paths); `/tmp*` is now ignored so they no longer pollute
  `git status`, and the inventory is in `artifacts/tests/repo-hygiene-1/inventory.txt`.

## Reproduce

```bash
# model + tests
cp -R qemu-a6400/qemu-overlay/. ~/sony-a6400-qemu-11.1.3/
cd ~/sony-a6400-qemu-11.1.3/build && ninja qemu-system-arm
cd "/mnt/c/Users/Administrator/Desktop/a6400 project"
python3 -m unittest tests.test_qemu_vph_composite tests.test_gate_checks -v

# Custom OS gate (Phase A)
bash custom_os/launcher/build_wsl.sh artifacts/tests/custom-os-build-34
python3 custom_os/hybrid/build_full_initrd.py --build-dir artifacts/tests/custom-os-build-34
python3 custom_os/hybrid/run_qemu_custom_kernel_verify.py \
    --build-dir artifacts/tests/custom-os-build-34 \
    --out artifacts/tests/qemu-custom-kernel-verify-26
```

## Next boundary

1. Map the framebuffer's full page set against the media range: the shm page 0 is not aliased, so a
   whole-buffer pagemap comparison (not one page) is needed to decide whether some pages overlap
   `0x23A80000..0x23C00000`. If they do, reserve the media range in the custom boot (the stock
   cmdline's `memrsv=704M@0x14000000:uc` form; a lone `memrsv=2M@0x23A80000:uc` hung the boot and
   must be replaced with the full stock reservation, not a partial one).
2. Only after `ui` passes in QEMU: Phase B loader branch (already green except `ui`,
   `qemu-ramboot-custom-full-1`), then the physical bundle from the same build (plan's manual step,
   deadman=120).
