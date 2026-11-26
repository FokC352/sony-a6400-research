# E0702 - ILCE-6400 Open Gate: FE band arithmetic, the 13-column sensor map, and the cross-body contract

- **Date:** 2026-11-26
- **Scope:** Offline read-only static analysis. No camera, USB, `adb`, `/dev/mem`, firmware write, or tracked-input modification.
- **Plan:** `docs/OPENCODE_SWARM_OPEN_GATE_PLAN_ROUND4.md`. Five read-only subagents covered disjoint slices; this is the coordinator synthesis with the load-bearing bytes re-checked in the coordinator session.
- **Predecessors:** `E0695`-`E0701`.

## 1. Inputs and the live matrix

| Item | Value |
| --- | --- |
| Reference binary | `artifacts/hardware/av-cam-camera-original.bin`, `16,803,048` B, SHA-256 `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209` |
| ORIL VA base / file offset / PA | `0x635C6000` / `VA - 0x635C6000` / `VA - 0x60000000` |
| Firmware tree | `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/` |
| Toolchain | `C:\Python314\python.exe` 3.14.6, Capstone 5.0.7, Unicorn 2.1.4 |

Note: `nflasha3_unpacked/av-cam.bin` in the tree is a **different build** (`16,674,456` B, SHA-256 `90d515cf...`), so its addresses are never used as reference VAs.

Live outcomes (`E0696`, `E0699`, `E0701`): PASS = `shrink` (-160 all FE), `test1` (sensor `Y_SIZE=4020` only), `timing4` (timing only), `plus24_rcvonly` (sensor + 6 `rcv_desc` +24, FE stock). FAIL `Lost CAMOUT_V` with zero `rcv_ovf`/`sdvga_ovf` = `plus8` (FE +8), `plus24`, `plus24_norcv`, `plus24_fe[0..1)` (only `fe_rec+0x002`), `plus24_fe[1..66)`, `test4`. `plus24_r4` = black screen, battery pull. Every FAIL has FE vertical max `>= 3453`; every PASS has FE vertical max `<= 3445`.

## 2. The FE band register is `(end : start)`, and stock proves the FE accepts 4024 lines

`0x63CDC30C` (function pointer `0x63CDC30D` stored at `0x64517DF8`) programs the FE unit directly from the record through per-field packers, each ending in one 32-bit store at `base + 0x14300 + k`. The decisive pair is:

```
0x63CDBEEE  (r2<<16)|r1  ->  base+0x143A4        args: record 0x198 (r2), 0x19C (r1)
```

so the register holds `(rec+0x198 << 16) | (rec+0x19C & 0xFFFF)`, and the active height is `end - start + 1`:

| record | `+0x198` end | `+0x19C` start | `end-start+1` | `+0x00A` active | class |
| --- | --- | --- | --- | --- | --- |
| idx 6 (4K 24p) | 3441 | 50 | **3392** | 3392 | video |
| idx 5 (still) | 4107 | 84 | **4024** | 4024 | still |
| idx 6 + `plus24` | 3465 | 50 | **3416** | 3416 | video, patched |

Three consequences:

1. The patch moves only the **end** coordinate by design. Moving a start (`+0x19C`, `+0x1A4`, `+0x1AC`, `+0x1B4`, `+0x1C8`) would translate the band instead of growing it, which is why those ten dwords are deliberately outside the 66-offset set. `E0701` confirmed live that touching `rec+0x194` (`+0x428`) produces a black screen.
2. **The FE register set demonstrably accepts 4024 lines in stock**: `idx 5/8/12` are shipped records for drive keys `0x0B/0x37`, `0x4E/0x68`, `0x2F` that drive a 6048x4024 readout through the same `0x540` layout and the same `(end:start)` registers. A 3445-line ceiling inside the FE registers is therefore not a register-capacity limit.
3. `plus24` builds a self-consistent band (`3465-50+1 = 3416` = the requested height), so the stall is not a band-arithmetic error.

## 3. The 13-column sensor map: no column carries a ~4000-line readout

`reglist` `0x6454C484`, 125 rows x `0x14` (`u32 dest` + 16 value bytes), terminator `0x6454CE48`; columns 13-15 are zero in all 125 rows. Cell layout `[pad][u16 reg][u8 val]` with `dest` pointing at the value byte; per-column vertical fields:

| col | Y_START | Y_SIZE | Y_END | `2*START+SIZE` | modes |
| --- | --- | --- | --- | --- | --- |
| 0/1/2/3/4/5/8/12 | 106 | **1132** | 1238 | 1344 | still/preview |
| 6 | 338 | 1138 | 1476 | 1814 | 11, 12 |
| 7 | 542 | 998 | 1540 | 2082 | 13 |
| 9 | 30 | 708 | 738 | 768 | 14 |
| 10 | 650 | 2780 | 3430 | 4080 | 15 (4K30p) |
| 11 | 342 | 3396 | 3738 | 4080 | 16, 17 (4K24p/25p) |

OBSERVED: **the largest `Y_SIZE` in the whole programme is 3396 (column 11)**. No column encodes ~4000 lines.

- **Rows 10-13 are the only per-mode vertical knob.** Columns 9, 10 and 11 differ from each other in exactly those four rows (`0x000A`-`0x000D`), so 708 / 2780 / 3396 are three settings of one knob.
- The five modes whose `size_rows` V is 4000 (keys `0x0B, 0x37, 0x68, 0x4E, 0x2F`) select columns 0/1/2, which carry the **boot default** `106/1132` - byte-identical to the untouched staging initial values (`0x645A1F24 = 0x04`, `0x645A1DAC = 0x6C`). A 1132-line window cannot produce a 4000-line still, so the still readout class is **not expressed by rows 10-13 at all**.
- The still class is a **53-register** programme (`0x0000` `0x0C`->`0x04`, `0x0001` `0x41`->`0x04`, the `0x002C`-`0x0033` analog group, `0x0038`-`0x0043` PGA group, `0x006F`/`0x08F1`/`0x0924`/`0x0ABA` and others), i.e. a register class switch, not a window tweak.

Corroborating negatives: the 65 staging cells not referenced by `reglist` are mode-invariant; the 124-entry send list `0x6454CF54` and the 49- and still-packet lists carry no line total; `0x6454CE48..0x6454E000` holds pointer lists and the typeinfo `0x6454D14C = 0x63F10AE4`; and a 344-hit resynchronised reference sweep over `0x6379B000..0x637A3000` finds only two readers of `0x63F109E1` (the applicator and a compare helper) and no VMAX/EBD/`Y_OUT_SIZE`/OP register.

The one driver-side height decision is `0x6379F00C` at `0x6379F034..0x6379F048`: for drive keys `{0x0B,0x37,0x68,0x4E,0x2F}` it compares a caller-supplied `u16` against `h16(size_rows[idx]+0x3C)` and branches to `0x6379E3DC` (request <= V_active) or `0x6379E43C` (request > V_active). Both branches only set state (`[this+0x42C] = 2` or `1`) and call `0x63CE80EC`/`0x63CE80BC`; **neither writes a sensor register**.

## 4. FE record census: the 66-offset set is complete for vertical ends

All 16 records at `0x63E12F14`, stride `0x540`. `idx 6`/`idx 9` are **4K 24p and 25p** (the same drive key, two variants), never simultaneous; they differ in 75 halfwords, all outside the 66 set, which is why patching both records identically is self-consistent.

| class | records | `+0x002/+0x00A/+0x012/+0x190` | `+0x20E` | `+0x258` |
| --- | --- | --- | --- | --- |
| video | idx 6, 9 | 3442/3392/3376/3445 | 5818 | 4752 |
| still | idx 5, 8 | 4108/4024/4000/4111 | 13781 | 4128 |
| still (aspect) | idx 12 | 4108/4024/4000/4111 | 13185 | 5208 |

Coordinator-verified: `+0x20E` is **not** a height ratio across records (`5818*4024/3392 = 6902`, but the still records carry 13781/13185), so the port's `+0x20E` rule is a single-record extrapolation.

Census of every halfword in `3000..5000` per record: for the still records 38 of 41 are in the 66 set; the three outside are `+0x258`, `+0x268`, `+0x2CE`, which **cannot be heights** because `idx 0` has 442 active lines with `+0x258 = 3576` and `+0x2C8` splits 868/15100 between 24p and 25p while every 66-set value is identical. **No vertical end or line count is outside the 66-offset set.**

The fields that *do* differ between a working 4024-line record and the 3392-line record but are not patched are the **band starts** (`+0x006 = 84` vs `50`, `+0x00E = 96` vs `58`, and the `+0x19C/+0x1A4/+0x1AC/+0x1B4/+0x1C8 = 84` vs `50` group), plus the half-scale path `+0x052`-`+0x0D2` (`1196..1188` vs `2388..2376`) and `+0x0D6/+0x0DE` (`598` vs `396`). Leaving the starts stock is correct for a symmetric `dy`; the half-scale path is the one vertical family the open-gate set never moves.

Also OBSERVED: `0x6376ECFC` writes 21 dwords from `r1+0x88+4i` into unit offsets `{0x420,0x428,...,0x3C80}`; since the FE descriptor is a copy of `rec+0x108`, `out+0x088 = rec+0x190`, which is the `E0700` mapping. The record is additionally programmed directly by `0x63CDC30C` at `base+0x14300..0x143C8`.

## 5. Cross-body: the ILCE-6400 4K vertical contract is a family constant

Three local bodies compared by pattern (not by absolute VA): reference ILCE-6400 `dd8f8d0c`, ILCE-6400A 1.01 (`3029a174`), ZV-E10 2.03 (`bc6cba98`).

| Structure | 6400 | 6400A | ZV-E10 |
| --- | --- | --- | --- |
| `fe_rec_0a` (1344 B) | 3442/3392/3376/3445/5818 | 0 differing bytes | 0 differing bytes |
| 16-record table, `idx 5/8/12` | 4108/4024/4000/4111 | identical | identical |
| reglist col10-vs-col11 rows | `[10,11,12,13]` | `[10,11,12,13]` | `[10,11,12,13]` |
| timing `[16]`/`[17]` | `(862,1740,1,29,27)` / `(827,1740,1,29,17)` | identical | identical |
| timing rows with `h1 != h2` | 0 of 19 | 0 of 19 | 0 of 19 |
| band offset table `0x6454AC44` | present | present | present |
| `Lost CAMOUT_V` / `S-Ele Refresh ng` strings | 1 each | 1 each | 1 each |

Coordinator-verified: every occurrence of the packed band words `0x03370D75`, `0x03270D71`, `0x00090032`, `0x03200D71`, `0x02F20D73` lies inside the FE record table range (`0x63E14xxx`/`0x63E15xxx`) - **no second stock copy of the FE vertical contract exists in the image**. On all three bodies `nflasha15_unpacked/kmod` holds no imager/CAMIN/DFE/CAMOUT module (`liro.ko`, `dmm.ko`, `stream.ko`, `mag.ko`, `codec_drv.ko`, `accel.ko`, `grm_gles.ko` are the camera-specific set, and `dfe_app.bin` is only 1,956 B).

A7 III comparison: `E0477` places ILCE-7M3 on the same `CXD90045` SoC (CE Linux 8.2.7.6, 24,351 shared symbols), so "different SoC generation" is not the explanation. Per `E0695`, the A7 III sensor column carried **three extra bytes** ("OB/extra") that the IMX284 programme does not implement (section 3 shows rows 10-13 are the only per-column vertical rows), and the A7 III timing row had `h1 != h2` while every local body has `h1 == h2` - a second independent period the ILCE-6400 table cannot express. No ILCE-7M3 image exists locally, so the identity of those three bytes and the second period remains unverified.

## 6. Rejected hypotheses

| # | Hypothesis | Verdict |
| --- | --- | --- |
| 1 | FE band clamp pair `+0x428`/`+0x42C` (R4) | **Refuted live** (`E0701`, black screen). |
| 2 | An FE vertical field outside the 66 offsets | **Refuted**: every vertical end/line count is inside the set (section 4). |
| 3 | FE register capacity at 3445 | **Refuted**: stock still records drive `end=4107`, active 4024 (section 2). |
| 4 | Band arithmetic error under `plus24` | **Refuted**: `3465-50+1 = 3416` equals the requested height (section 2). |
| 5 | Sensor `Y_SIZE` as a 4000-line readout | **Refuted**: no column carries ~4000 lines; still modes inherit the 1132 boot default and the still class is a 53-register switch (section 3). |
| 6 | `rcv_desc`, DMM, numeric budgets, SGC, receiver line cap | **Refuted** in `E0698`-`E0700`. |
| 7 | A second stock copy of the FE band geometry in the image | **Refuted**: band words occur only inside the 16-record table (section 5). |
| 8 | A per-body quirk | **Refuted**: the contract is identical across three bodies and builds (section 5). |

## 7. What the open-gate set can and cannot move

It can move, and provably does: the FE vertical **ends** (66 offsets), the FE band registers built from them, `size_rows+0x3C`, `rt_t14+0x50`, the 8 timing slots, and the 6 `rcv_desc` halfwords.

It cannot move: the sensor readout **class** (53 registers, including `0x0000`/`0x0001`), the sensor still line count (sensor-internal), the FE band **starts**, the half-scale vertical path (`+0x052`-`+0x0D2`), the `idx 5/8/12`-style companion fields, or anything on the CAMOUT publish side (section 8).

Given `test1` and `plus24_rcvonly` pass, the sensor-side lever is not the limiter; given `plus8` fails, the FE-vertical lever is a hard one-sided boundary; and given sections 2 and 4, that boundary is not inside the FE registers. It is therefore a comparison made against a stock-valued reference outside the record-programmed registers.

## 8. Remaining boundary

Two of the three `E0700` candidates are now closed:

- **R6 (`0xF3122000`/`0xF3123000`) is refuted.** The block is written by `MIF_SetMIFMode` `0x63799C8C` from the constant ROM descriptor table `0x63E25D24` (38 records, stride `0x14`, second half at `0x63E25EA0`; mode 16 = index 16 = record `0x63E25E64`). All five consumed fields (`+0x0C`, `+0x0D`, `+0x0E`, `+0x10`, `+0x12`) are **zero in every one of the 38 records**, so the stock writes to `+0x138`/`+0x144`/`+0x148`/`+0x160` are `0` on both channels; `0x63799C8C` contains no compare and no abort path; and the block has no in-image reader. The patch plan does not touch that table (0 overlapping slots for all eight steps), so its content is byte-identical in `plus24_rcvonly` (PASS) and `plus24_fe[0..1)` (FAIL).
- `E0700`'s premise that `0x63799D04` has no static entry is **refuted**: the function starts at `0x63799D04` with `ldr r2,[r1]` before its own `push`, and its only static transfer is a `b.w` at `0x63CF0628` inside the 4-byte thunk `0x63CF0626`, reached from `InterfaceCtrl::SetImgIfRequest` `0x6379FDF0` (vtable slot `0x6451955C`). The chain is complete and entirely in-image.

**R5 is closed to an architectural boundary: nothing in the shipped ARM software publishes or invokes it.** OBSERVED:

- `0x645CAA40` is not a callback table entry. It is slot **#9386** (byte offset `0x92A8`) of one flat `u32` array at `0x645C1798` (file `0x00FFB798`) that ends exactly at EOF `0x645CC4E8` - 11,092 slots, 11,085 distinct values, 4,361 in the code range. The ORIL header field `[5] = 0x635C60B4` points at `{0, 0x644F1000, 0x000D0798}` and `0x644F1000 + 0xD0798 = 0x645C1798`, so the declared ram region ends exactly at the array base; the same shape exists in the other build. It is the image's PIC data segment, not a registrar-built table.
- `0x639303D1` occurs exactly once in the file (`0x01004A40`) - it is the slot's **link-time initial value**. There is no writer: no `movw/movt` builds any array address, no literal equals `0x645CAA40`, and the only two code sites that touch offset `0x92A8` (`0x638F8A94`, `0x639337D4`) load it into a formatter (`0x63D22C36`, publisher ids `0x17`), never store and never `blx`.
- No branch reaches `0x639325C4` or `0x639303D0` (all A32/T32 B/BL/BLX/Bcc.W decoded), no `u32` literal equals either entry, and no table slot holds them. The only data pointer to `0x639325C5` is a PIC-computed address at `0x639330C4`/`0x63933506` passed to the formatter `0x63D0F560` - it exists to print the address.
- Cross-binary: `liro.ko` validates the ORIL header and `blx`-calls `entry(pre) = 0x635C6044`, never the data table; `im.elf` is the launcher of `/system/av-cam.bin`. No `.ko` (`liro`, `upm`, `mcmn_drv`, `mag`, `dmm`, `stream`, `stream2`, `osal_uipc`, `osal_utm`, `utimer`) and no blob (`dfe_app`, `dfe_dat`, `idt_cam`, `sa_dfdet`, `ssboot`, `bootin.elf`, `bonobo.bin`, `lif_app.bin`, `ldr_drv.bin`) contains `CAMOUT`, `CAMIN`, `vfc`, `frc`, `S-Ele` or any CAMOUT address. No module holds a stock height as a data word.
- The commit body additionally writes a BSS cell `0x64369138` (`0x6393040E str r5,[r3]`), which is distinct from `p[0]/p[1]` and does not affect the watchdog predicate.

So both the `(vfc, frc)` pair and the frame-commit invocation are published from outside the shipped ARM software - the CAM front end on the other side of the ORIL boundary - and the stock vertical reference that gates them is not a software constant anywhere in this firmware tree. Two in-image carriers remain candidates for the *programming* half, and both are hardware-facing: the `0x370`-byte per-channel register image programmed by `0x6375FFAC` to `0xF3851100/0x1200/0x1300/0x1400` (fields `+0x258 -> 0xF38513A8`, `+0x2C8 -> 0xF3851418`, `+0x2CE -> 0xF385141E`, producer unclosed) and the 19-word scatter `0x6376E518` from `out+0x3D8` into unit offsets `{0xD08, 0xD20-0xD40, 0xD90-0xDA0, 0x15034, 0x150CC-0x150D8}`.

## 9. Recommended next read-only checks (need explicit user GO)

1. Read `0xF3851418`, `0xF385141E`, `0xF38513A8`, `0xF38513B8` and the `0xF3951xxx` twins during a `plus8` run: if those scalars track the patched record, the `0x370` image is a transform of the record; if they stay stock, it is a second, unpatched reference. Device-map pages, so each address needs per-address go-ahead under `E0647` and must pass `tools/devmem_policy.py`.
2. Read `[[parent+0x8C]]+0x3D8` (19 dwords) under mode 16 to see whether the scatter source is zero, stock, or dy-tracked.
3. Keep `noreboot` set, and restore `putback`, `noreboot off`, `resetcnt`, `158/158 STOCK` afterwards.

## 10. Repeatability and receipts

- `C:\Python314\python.exe -m tools.research.a6400_open_gate --verify` - 8/8 `VERIFY PASS`, `putback=EXACT`, `patches=152` each.
- `C:\Python314\python.exe -m unittest tests.test_a6400_open_gate -q` - `Ran 5 tests ... OK`.
- `C:\Python314\python.exe -m tools.research.knowledge_map build` / `check` - `knowledge map OK`.
- Coordinator byte checks: the 16-record `+0x002/+0x00A/+0x190/+0x20E/+0x258` census; band-word duplication scan; `0x63CD0FB0` writing `+0x43C`; `0x6376E518` reading the `0x6454AB60` offset table.
- Scratch scripts lived under `artifacts/scratch/0702/` (gitignored); the repository root was left clean.
- `tools/research/a6400_open_gate.py` and `a6400_og_ram_table.h` were **not** modified.

## 11. Superseded conclusions

- The plan's `R5` alternative "the true sensor frame length may still be stock because the patch is incomplete" is **refuted**: no column carries a 4000-line readout, so no row-10-13 patch can express the still class; the still line count is sensor-internal (section 3).
- `E0700` ranking item R4 is **refuted live** (`E0701`).
- `E0700` ranking item R6 is **refuted offline** (section 8): the `0xF3122000`/`0x3000` fields are programmed from the unpatched constant table `0x63E25D24` and are all zero for mode 16.
- `E0700`'s statement that `0x63799D04` has "no static caller, no pointer and no pc-relative target" is **refuted**: there is exactly one static transfer, a `b.w` at `0x63CF0628` reached from `InterfaceCtrl::SetImgIfRequest`, invisible to `BL`-only scans because the function starts before its own `push` (section 8).
- `E0700` treated `0x645CAA40` as a callback slot filled by a registrar. That is **corrected**: it is slot 9386 of the ORIL data array at `0x645C1798` with a link-time value, and nothing in the shipped software writes or invokes it (section 8).
- The `E0700` statement that `+0x20E` scales with height is **corrected**: it does not hold across records (section 4).
