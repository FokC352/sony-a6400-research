# E0700 - ILCE-6400 Open Gate: the FE band registers, the `+0x002` MMIO trace, and the exonerated upstream stages

- **Date:** 2026-11-26
- **Scope:** Offline read-only static analysis of why *any* FE vertical field raised above its stock value stalls `CAMOUT_V`. No camera, USB, `adb`, `/dev/mem`, firmware write, or tracked-input modification.
- **Plan:** `docs/OPENCODE_SWARM_OPEN_GATE_PLAN_ROUND3.md`. Five read-only subagents covered disjoint slices; this record is the coordinator synthesis, with every load-bearing byte re-checked in the coordinator session.
- **Predecessors:** `E0695`, `E0696`, `E0697`, `E0698`, `E0699`.

## 1. Inputs, identity, and the live matrix

| Item | Value |
| --- | --- |
| Binary | `artifacts/hardware/av-cam-camera-original.bin` |
| Size / SHA-256 | `16,803,048` B / `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209` |
| ORIL VA base / file offset / PA | `0x635C6000` / `VA - 0x635C6000` / `VA - 0x60000000` |
| Toolchain | `C:\Python314\python.exe` 3.14.6, Capstone 5.0.7, Unicorn 2.1.4 |
| Planner (unmodified) | `tools/research/a6400_open_gate.py`, `tools/research/a6400_og_ram_table.h` |

Every live outcome below is from `E0696`/`E0699`. The FE vertical maximum is computed from the 66-offset list applied to `fe_rec_0a`/`fe_rec_19` (coordinator script, deleted):

| Case | What moved upward | FE max | Live |
| --- | --- | --- | --- |
| stock | nothing | 3445 | PASS |
| `test1` | sensor `Y_SIZE=4020` only | 3445 | PASS |
| `timing4` | timing ROM + `rt_t14` only | 3445 | PASS |
| `plus24_rcvonly` | sensor + 6 `rcv_desc` +24 | 3445 | PASS, `refresh_cnt=0` |
| `shrink` | all 66 FE offsets -160 | 3285 | PASS, `refresh_cnt=0` |
| `plus8` | all 66 FE offsets +8 | 3453 | FAIL `ref=39` |
| `plus24` | all 66 FE offsets +24 | 3469 | FAIL |
| `plus24_norcv` | all 66 FE offsets +24, `rcv_desc` stock | 3469 | FAIL `ref=44` |
| `plus24_fe[0..1)` | **only `fe_rec+0x002`** 3442 -> 3466 | 3466 | FAIL `ref=17` |
| `plus24_fe[0..2)` | `+0x002`, `+0x00A` | 3466 | FAIL `ref=19` |
| `plus24_fe[0..4)` | first 4 offsets | 3466 | FAIL `ref=32` |
| `plus24_fe[0..8)` | first 8 offsets | 3466 | FAIL `ref=53` |
| `plus24_fe[0..16)` | first 16 offsets | 3466 | FAIL, severe stutter |
| `plus24_fe[0..33)` | first 33 offsets | 3469 | FAIL `ref=20` |
| `plus24_fe[1..66)` | **all except `+0x002`** | 3469 | FAIL `ref=22` |
| `test4` | all 66 FE offsets +160 + timing | 3605 | FAIL |

**The separation is exact and one-dimensional:** every PASS has FE max `<= 3445` and every FE-patched case has FE max `>= 3453`. `plus8` fails with `FE max = 3453` against a nominal 3480-line period, i.e. 27 lines of slack, so this is not a budget. The rule is **any FE vertical value above its stock value stalls**, and `+8` is already enough.

## 2. The two fatal subsets converge on the same registers

This is the central new result of Round 3.

- `0x63771C78` selects the record (`key 0x1F/0x89`, variant `0x0A` -> `0x63E14E94`, `0x19` -> `0x63E15E54`).
- `0x63CCF5C6(rec, out)` copies `rec+0x108..` (0x438 bytes) to `out+0x000`, then expands the leading 20-byte records: `rec+0x000 -> out+0x438`, **`rec+0x002 -> out+0x43C`**, `rec+0x004 -> out+0x440`, `rec+0x006 -> out+0x444`, `rec+0x008 -> out+0x448`, **`rec+0x00A -> out+0x44C`**, and so on.
- `0x63CD1D1C` then copies ten words `out+0x438..0x45F` into the FE band object at `obj+0x20..0x47` (`0x63CD1D8C bl 0x63CCFFD8`), so **`rec+0x002` -> band `+0x24`** and **`rec+0x00A` -> band `+0x34`**.
- The same routine writes 21 registers through a table loop (`0x63CD1D2E bl 0x6376ECFC`) whose source is `out+0x088+4i` = `rec+0x190+4i`. **`rec+0x002` and `rec+0x00A` are outside that window and therefore never enter the register table.**

So `plus24_fe[0..1)` changes no register-table entry at all, yet stalls identically to `plus24_fe[1..66)`. The two subsets must converge further down. They do: the band-apply pass `0x63CD1D1C` read-modify-writes the *same* registers the table initialises:

```
0x63CD0F84  ldr.w r0,[r0,#0x438]   ; read current V_end/H_end
0x63CD0FA0  lsls  r3,r0,#0x12 ; lsrs r3,r3,#0x12   ; V = bits[13:0]
0x63CD0FA2  lsrs  r0,r0,#0x10 ; lsls r0,r0,#0x12 ; lsrs r0,r0,#0x12  ; H = bits[29:16]
0x63CD0FB0  lsls  r2,r2,#0x10 ; adds r2,r2,r1 ; str.w r2,[r0base,#0x43c]
0x63CD20CC  bl    0x63CD0F84      ; pan reads +0x438
0x63CD20DA  bl    0x63CD0FB0      ; pan writes +0x43C
```

and the pan delta is carried in the band object built from the record's first 20 bytes (**INFERRED**: `0x63CD1D1C` reads the delta from the object at `0x63CD1D8C`; the alternative that `band+0x24` is consumed by a registered object method is subagent 1's open boundary R-C and cannot be excluded statically). Therefore:

- raising `rec+0x190+4i` (the `[1..66)` subset) raises the table-seeded `+0x438`/`+0x43C`;
- raising `rec+0x002` (the `[0..1)` subset) raises the band-object delta that the pan adds to the same `+0x438`/`+0x43C`.

Both routes end at the same two registers, which is why either half is independently fatal and why their union (`plus24`) is too. The 20 offset pairs that are **not** in the 66-offset set (the `50`/`9` band starts and the `0`/`1` control words) are what keep the band anchored.

## 3. The 21-register FE band table (OBSERVED, coordinator-verified)

Table at `0x6454AC44`, 21 dwords, read by `0x6376ECFC` (`cmp r3,#0x54`, `str r4,[r0,r2]`), source `rec+0x190+4i`:

```
offsets: 0x420 0x428 0x42c 0x430 0x434 0x438 0x43c 0x440 0x444 0x448
         0x44c 0x450 0x454 0x490 0x458 0x45c 0x488 0x48c 0x408 0x40c 0x3c80

i=0  rec+0x190 0x03370d75  V=3445 H=823      i=12 rec+0x1c0 0x03300d75 V=3445 H=816
i=1  rec+0x194 0x00000000  V=0    H=0        i=13 rec+0x1c4 0x03350d75 V=3445 H=821
i=2  rec+0x198 0x03270d71  V=3441 H=807      i=14 rec+0x1c8 0x00090032 V=50   H=9
i=3  rec+0x19c 0x00090032  V=50   H=9        i=15 rec+0x1cc 0x03270d71 V=3441 H=807
i=4  rec+0x1a0 0x03200d71  V=3441 H=800      i=16 rec+0x1d0 0x00000000 V=0    H=0
i=5  rec+0x1a4 0x00090032  V=50   H=9        i=17 rec+0x1d4 0x00000d75 V=3445 H=0
i=6  rec+0x1a8 0x03200d71  V=3441 H=800      i=18 rec+0x1d8 0x00000000 V=0    H=0
i=7  rec+0x1ac 0x00090032  V=50   H=9        i=19 rec+0x1dc 0x00000001 V=1    H=0
i=8  rec+0x1b0 0x03200d71  V=3441 H=800      i=20 rec+0x1e0 0x00000000 V=0    H=0
i=9  rec+0x1b4 0x00090032  V=50   H=9
i=10 rec+0x1b8 0x02fa0d71  V=3441 H=762
i=11 rec+0x1bc 0x02f20d73  V=3443 H=754
```

The word is `V | H<<16` (bits[13:0] = V, bits[29:16] = H), proven by the accessor slicing above. The pattern is a set of crop bands: each "end" word (V 3441..3445) is paired with an unpatched "start" word `0x00090032` (V=50, H=9), and `3441 - 50 = 3391` matches the FE active height `3392` within one line. The 66-offset patch set moves only the "end" words of each pair.

Two consequences:

1. The FE record **format** supports far more than 3442 lines: table records idx 5/8/12 carry `+0x002/+0x00A/+0x190 = 4108/4024/4111` in stock configurations, so "3442 is an FE-format limit" is refuted.
2. `rec+0x194` (-> `+0x428`, value 0) is **not** in the 66-offset set while its partner `rec+0x198` (-> `+0x42C`, value 3441) is. The clamp pair therefore scales asymmetrically under any `dy != 0`. This is recorded as an observation for candidate R4 below, not as a proven cause.

## 4. `rec+0x002` and `rec+0x00A` are crop-corner V coordinates, not totals

The record's first 20 bytes are `{H=3232, V=3442, dx=36, dy=50, H=3196, V=3392, dx=48, dy=58, H=3184, V=3376}` and the arithmetic closes exactly: `3232 - 36 = 3196` and `3442 - 50 = 3392`. So the triple is *corner A, delta, corner B*, and `+0x002`/`+0x00A` are V coordinates.

Corollary for the live matrix: the 66-offset set raises `+0x002` and `+0x00A` by the same `dy` while leaving `dy = 50` untouched, so the corner relation is preserved in every all-or-none case. A self-consistency failure inside the record is therefore **not** the cause: `plus24_fe[0..2)` preserves the relation (`3466 - 50 = 3416`) and still fails (`ref=19`).

## 5. The sensor programming path is transparent, but `Y_SIZE` is not the frame length

OBSERVED, all from the sensor driver:

- `0x6379D618` is the only `reglist` applicator, and it is called from exactly one site in the imager module, `0x6379F3FA` inside `C_ImagerCtrl_IMX284::SetMode` (`0x6379F380`, vtable slot `0x028`, vtable `0x64519558`, typeinfo name `N6Camera6Imager6IMX28419C_ImagerCtrl_IMX284E` at `0x63F10AE4`). There is **no per-frame application**: the image-wide `ldr.w rN,[rN,#0x100] ; blx rN` sweep finds 37 sites and only this one is in the imager module.
- All dynamic sensor writes funnel through two senders into one command buffer (`0x6379F27C` -> `0x63CF039A` -> `0x63CF0320 strb`), with no mask and no clamp. The 124-entry list at `0x6454CF54` contains `0x000A`-`0x000D` and points at the **live staging cells**, so a re-send transmits the patched values.
- No duplicate stock list exists, and the second `reglist` at `0x64200EB4` is BSS-zero with a slot (`0x645C4A00`) that is never materialised; it writes nothing.
- Therefore the patched bytes **do** reach the wire.

But `Y_SIZE` is not the emitted frame length:

- Column 0 (the column for the 6000x4000 still modes) writes `Y_SIZE = 1132`, which cannot be a 4000-line window.
- Stock column 11's window bottom is `342 + 3396 = 3738`, already above every FE/RCV total.
- No register in the 125-row programme or in the 124-entry send list encodes a line total, `VMAX`, `WIN_V`, MIPI `Y_OUT_SIZE`, EBD position or SLVS packet count.

So `test1` and `plus24_rcvonly` passing prove only that a *window* change is harmless; they do **not** prove the sensor emitted more lines. The frame length is set outside this driver (sensor-internal sequencer or the SGC-driven period).

## 6. The receiver has no static line cap

OBSERVED negatives in `0x63798C00-0x6379B000` (`GCS2RXLINK`, `CSI-2 Rx`, `SubLVDS`, `ComboPHY`):

- No `movw/movt` immediate in `3000..3700`; only `0x63799DCA/DEA mov.w r1,#0xC80` (a wait argument).
- Link blocks `0xF3E00000` (MAINLSI) and `0xFC0F0000` (DFE) are written only with lane/PLL constants (`0xAA, 0x60, 0x57, 0x30D, 0x309, 0x30C, 3, 5, 0xA, 0x11, 9`) and lane-struct bytes.
- The only frame check (`0x63799ACC`) accumulates `+0x7C/+0xB0/+0xB8` and compares against a runtime expected value from `*(global+0xC0)`; it logs, and it would raise `rcv_ovf`, which stayed `0` in every failing run.

The closest exact candidate is the SGC-adjacent window written by `0x63799C8C` (`0xF3122000`/`0xF3123000` `+0x144`, `+0x148`, `+0x160[29:16]`, `+0x160` bit0) from `struct+0x10/+0x12`, reached only through `0x63799D04`, which has **no static caller, no pointer and no pc-relative target**. That remains the one receiver-adjacent register block whose stock value is not statically constrained.

## 7. SGC1/SGC2/SGC3 are exonerated

OBSERVED:

- The live `sgc1_win` bytes are not a mystery: `block+0x38..+0x42` is a **verbatim copy of one `timing_rom_ntsc` record**. Record layout `{u32 H; u32 V; u8 c; u8 d; u8 e; u8 pad}` at `0x63E2580C`, 12-byte stride, duplicated at +12:

  ```
  0x63E2592C  H=858 V=1750 c=0 d=0  e=0    (MSC)
  0x63E2598C  H=862 V=1740 c=1 d=29 e=27   (mode 16, Movie 4K 24p)
  0x63E259A4  H=827 V=1740 c=1 d=29 e=17   (mode 17, Movie 4K 25p)
  ```

  The byte triple `01 1D 1B` occurs exactly four times in the whole image, all in this table. `0xF312026C = (27<<16)|29` follows mechanically, and `0xF3120264 = ((H-1) + sxtb(block[0x40]))<<16 | 1` when both `block[0x41]` and `block[0x42]` are non-zero.

- `0x6376910C` is `SgcDev::UpdateWindow`-style trim: it reads `meas` and the DFE parameter, runs a 32:8 phase servo (`0x637691D6 movs r1,#8`, `0x63CCFA54 = r0 & -r1`, applied after `>>2`), folds a one-shot trim (consumed at exit: `dev+0x70 = dev+0x74 = 0`), and writes only phase/period words (`+0x32C, +0x330, +0x334, +0x33C, +0x35C, +0x360, +0x364, +0x438, +0x43C, +0x470, +0x474, +0x478, +0x488, +0x48C, +0x20C`). It writes **no** start/enable/command register and has no abort path.
- Its two static callers are `0x63CCD0AA` and `0x63CCD9E0`, both inside mode/timing apply wrappers; the function is also a member of the 12-slot command table at `0x645171FC..0x645176EC`. Cadence is mode/timing change, not per frame.
- The block `0x64339DF8` is written by `0x638B6124` (`memcpy(dst=0x64339DF8, src=X+0x15C, 0x144)`, sole caller `0x63932A6E`), by `0x638B8CE8` (field copy from `src+0x98/+0x9C/+0xA0`), and zeroed by `0x638B57E6`. `block+0x41/+0x42` come only from `X[0x19D]/X[0x19E]`, i.e. the timing record.
- `0x63CE6630..0x63CE677C` is **not** an SGC save/restore: it is a read-only snapshot of `0xF3021100`/`0xF3021200` (a different unit) into a RAM object, and no mirror routine writes those registers back. No SGC register has a RAM shadow that could be restored stale.

**Decisive partition:** every one of the 16 live runs holds the same mode 16/17, hence the same `timing_rom_ntsc` record, the same `block+0x40..0x42`, the same `0xF3120264/0x026C`, the same hvcyc inputs and the same trim-loop state. `plus24_fe[0..1)` (FAIL) and `plus24_rcvonly` (PASS) are byte-identical on the entire SGC side. A differentiator that is identical in a PASS and a FAIL cannot be the cause. **SGC is passive/downstream, and no FE record field can reach an SGC register on this path.**

This refutes `E0698` `C27`/R3 (the "SGC2 measured+trim closes one-sidedly" hypothesis).

## 8. Corrections to earlier records

- **`0x6454AB60` is not a height list.** It has a real reader, `0x6376E518` (called from `0x63CD0C66`), which treats it as a 19-entry **struct byte-offset** table: `r4 = [r1+r3+0x3D8]`, `r2 = table[r3]`, `[r0+r2] = r4`, `r3 += 4` until `0x4C`. The words read as "3360, 3368, 3372, 3376, ... 3472, 3480" are the offsets `0x0D20, 0x0D28, 0x0D2C, 0x0D30, ... 0x0D90, 0x0D98`. `E0698` section 7's "height-keyed table with no reader" and the "3480 VMAX datum" reading are **retracted**.
- `0x63E58000` is a diagnostic `(value, MMIO address)` dump block read by the formatter `0x63BC3EE8 -> 0x63BC52B4`, not a programming source.
- `0x64547D38/0x64547D3C` are the low halves of the APL DMA addresses `0xF3850D30`/`0xF3950D30`, not line counts.
- The `0x63908394` tail of the `Lost CAMOUT_V` watchdog does **not** decide whether the event is logged. Both of its arms (`0x63907E32` and `0x63907E40`) reach the log at `0x63907E66`; it only selects whether `hq`/`vq` are populated from `0xF3120318`. The `imaging->[0x514] == 0` test at `0x63907E2A` is still required to report.

## 9. Rejected hypotheses (all evaluated against the full 16-outcome matrix)

| # | Hypothesis | Verdict |
| --- | --- | --- |
| 1 | `rcv_desc` mismatch (R1) | **Refuted live**: `plus24_rcvonly` patches it and passes; `plus24_norcv` keeps it stock and fails. |
| 2 | Numeric line budget (`3480 = 2 x 1740`) | **Refuted**: `plus8` fails with 27 lines of slack; the `3480` datum was a struct offset (section 8). |
| 3 | FE record internal inconsistency (`V_A - dy == V_B`) | **Refuted**: `plus24_fe[0..2)` preserves it and still fails. |
| 4 | Sensor `Y_SIZE` clamps the emitted lines | **Refuted as the only cause**: `test1`/`plus24_rcvonly` pass with FE stock; and `Y_SIZE` is not the frame length at all (section 5). |
| 5 | The `reglist` patch never reaches the wire | **Refuted**: single applicator call per `SetMode`, 124-entry re-send reads live cells, bare `strb`, second table inert. |
| 6 | A duplicate FE record / second column table pins stock values | **Refuted**: only idx 6/9 are reachable for keys `0x1F/0x89`, and no other of the 16 records matches idx 6 on the 66 vertical offsets. |
| 7 | `rt_t10`/`rt_t14`/`rt_t18`/`rt_t1c` drive timing | **Refuted for this image** (`E0698` section 8.6); `timing4` passes. |
| 8 | SGC1 `H/V`/`hvcyc` latched stock | **Refuted**: `test4` reached `(824,1820)` and still failed. |
| 9 | SGC2 measured+trim one-sided loop (R3) | **Refuted**: SGC state is byte-identical in `plus24_fe[0..1)` FAIL and `plus24_rcvonly` PASS; the trim loop writes no gate. |
| 10 | SGC save/restore re-applies a stale window | **Refuted**: the snapshot is of a different unit and there is no restore writer. |
| 11 | Receiver/link stops at a stock line count | **Unproven and unimplemented**: no static write in the receiver driver carries 3396/3442/3445/3447, and the frame check would set `rcv_ovf` (it stayed 0). |
| 12 | Any height compare with a vertical constant | **Refuted**: no `CMP` immediate equals 3376/3392/3396/3400/3416/3442/3445/3447/3456/3480 anywhere; the only video-height compare is `cmp.w #0x870` (2160). |

## 10. What remains, and the exact architectural boundary

Two facts bound the remaining search.

**Fact A - the failure is a stock-value comparison, not a budget.** The 66-offset set moves the FE band "end" words (`V 3441..3445`) while leaving their unpatched "start" partners at `V=50, H=9`. `+8` is fatal and `-160` is harmless, so a stock-valued reference is being exceeded, once, per moved field.

**Fact B - the publish side of CAMOUT is not in this image.** `p = *(0x643691FC+4)` and its fields `p[0]`, `p[1]`, `p[8]` have **no ARM writer**: no store through any of 205 PIC-resolved `0x643691FC` bases, no effective-address store to `0x64369200/04/08`, and no 32-bit literal of those cells. `CTX[0..2]` are `.bss` zeros in the file. The commit `0x639303D0` has no static caller either; its Thumb entry appears exactly once as data, `u32(0x645CAA40) = 0x639303D1`, in a table of function pointers, so it is invoked through a registered callback. The watchdog `0x63907D00` and its caller `0x639325C4` have no static referrer at all.

Consequently the `(vfc, frc)` pair that drives the commit is **published from the other side of the CAMOUT boundary** (sub-LSI/DSP or a bulk copy), and the ARM side only snapshots it (`0x643679DC`/`0x6436799C`) and compares (`0x63907E14`/`0x63907E20`). Any stock-valued FE vertical contract that the publisher enforces is therefore outside `av-cam.bin`.

Surviving candidates, all one-sided and none separable offline:

- **R4 (new, strongest static lead):** the FE band clamp. `0x6376910C`'s V pass compares `lo(+0x438)/lo(+0x43C)` against `lo(+0x428)` and `lo(+0x42C)`, which are programmed from `rec+0x194` (value 0, **not** in the 66-offset set) and `rec+0x198` (value 3441, **in** the set). The pair therefore scales asymmetrically, and the clamp rewrites `+0x438`/`+0x43C` whenever the requested end exceeds `+0x42C`; that rewrite moves the band start, which is exactly the kind of single-sided distortion that would prevent a vertical completion.
- **R5:** a stock-valued copy of the FE vertical geometry on the publish side of the CAMOUT boundary (Fact B), enforced one-sidedly.
- **R6:** the SGC-adjacent receiver window at `0xF3122000`/`0xF3123000` `+0x144`/`+0x160`, written from a struct whose base is not statically closed (`0x63799D04` has no referrer).

Nothing in this record justifies a write patch.

## 11. Recommended next read-only checks (need explicit user GO)

1. Read `0xF3120428`, `0xF312042C`, `0xF3120438`, `0xF312043C`, `0xF3120470`, `0xF3120474` and the twin block at `0xF3121428..` during a `plus8` run: this directly tests R4 by showing whether the clamp rewrote the band and where the start/end moved. Device-map pages, so each address needs per-address go-ahead under `E0647` and must pass `tools/devmem_policy.py`.
2. Read the SGC-adjacent receiver window `0xF3122000`/`0xF3123000` `+0x144`, `+0x148`, `+0x160` to test R6.
3. A single targeted write test separates R4 from R5 without touching the publish side: `plus24` with `rec+0x194`/`rec+0x198` **also** moved by the same `dy` (so the clamp pair stays consistent). If it passes, the clamp pair is the carrier; if it still fails, the reference is external (R5).
4. Keep `noreboot` set for any of the above, and restore `putback`, `noreboot off`, `resetcnt`, `158/158 STOCK` afterwards.

## 12. Repeatability and receipts

- `C:\Python314\python.exe -m tools.research.a6400_open_gate --verify` - 7/7 `VERIFY PASS`, `putback=EXACT`, `patches=152` each.
- `C:\Python314\python.exe -m unittest tests.test_a6400_open_gate -q` - `Ran 5 tests ... OK`.
- `C:\Python314\python.exe -m tools.research.knowledge_map build` / `check` - `knowledge map OK`.
- Coordinator byte checks: the 21-dword offset table at `0x6454AC44` and its `rec+0x190+4i` sources; the `V | H<<16` slicing at `0x63CD0FA0`/`0x63CD0FA2`; `0x63CD0FB0` writing `+0x43C`; `0x6376E518` reading the `0x6454AB60` offset table; `0x6393275E bl 0x63907D00`.
- Scratch scripts lived under `artifacts/scratch/0700/` (gitignored) and the repository root was left clean.
- `tools/research/a6400_open_gate.py` and `a6400_og_ram_table.h` were **not** modified.

## 13. Superseded conclusions

- `E0698` section 7 and `C26`: the `0x6454AB60` "height list" reading is **retracted**; it is a struct-offset table with reader `0x6376E518` (section 8).
- `E0698` `C27`/R3 (SGC2 measured+trim one-sided loop) is **refuted** (section 7).
- `E0698` section 2.2's tail-block sentence is **corrected**: the `0x63908394` block only selects whether `hq`/`vq` are populated (section 8).
- `E0698` ranking item 1 ("stock frame budget vs patched vtot") is **retracted**: `plus8` fails with 27 lines of slack.
- The plan's section 0.2 inference that "the sensor wire side may emit more lines" is **weakened**: `Y_SIZE` is not the frame-length control, and `test1` cannot be used as proof that the sensor emitted 4020 lines.
