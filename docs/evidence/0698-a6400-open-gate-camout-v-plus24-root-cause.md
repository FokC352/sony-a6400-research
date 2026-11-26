# E0698 - ILCE-6400 Open Gate `plus24`: `Lost CAMOUT_V` root cause and the upward-only vertical ceiling

- **Date:** 2026-11-26
- **Scope:** Offline, read-only static analysis plus bounded offline verification of why `dy = +24` (`plus24`) and `dy = +160` (`test4`) lose `CAMOUT_V` while `dy = -160` (`shrink`), `test1`, and `timing4` stay clean. No camera, USB, `adb`, `/dev/mem`, firmware write, or tracked-input modification. This record closes the `E0697` section 8 decision gate with the live `watch` trace already captured in `E0696` section 4 and does **not** re-run it.
- **Plan:** `docs/OPENCODE_SWARM_OPEN_GATE_PLAN_ROUND2.md`. Five read-only subagents covered disjoint subsystems; this record is the coordinator synthesis with every load-bearing byte re-checked in the coordinator session.

## 1. Inputs, identity, and method

| Item | Value |
| --- | --- |
| Binary | `artifacts/hardware/av-cam-camera-original.bin` |
| Size | `16,803,048` bytes |
| SHA-256 | `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209` |
| ORIL VA base | `0x635C6000` |
| File offset | `VA - 0x635C6000` |
| Physical (live) address | `VA - 0x60000000` |
| Toolchain | `C:\Python314\python.exe` 3.14.6, Capstone 5.0.7, Unicorn 2.1.4 |
| Patch planner | `tools/research/a6400_open_gate.py` (authoritative field lists) |
| Patch table | `tools/research/a6400_og_ram_table.h` (158 entries) |

Offline commands run for this record (all read-only):

```powershell
C:\Python314\python.exe -m tools.research.a6400_open_gate --verify
C:\Python314\python.exe -m unittest tests.test_a6400_open_gate -q
C:\Python314\python.exe -m tools.research.knowledge_map build
C:\Python314\python.exe -m tools.research.knowledge_map check
```

Methods used by this pass and by the subagents: Capstone Thumb-2 linear sweeps with literal-pool resolution (`ldr rX,[pc,#k]` -> `add rX,pc`), raw `BL`/`BLX` immediate decoding over every even offset, whole-image 16-bit and 32-bit immediate scans (including `MOVW`/`MOTW` pair resolution), absolute-`u32` pointer scans, and a PC-relative `adr`/pool sweep. Limits are stated at each negative claim.

## 2. What `Lost CAMOUT_V` actually is

### 2.1 Function, caller, and latch chain - OBSERVED

`0x63907D00` (`push.w {r4,r5,r6,r7,r8,lr}`, epilogue `0x63908390`) is the CAMOUT frame supervisor. Its single direct caller is `0x6393275E` inside `0x639325C4`. The log call and the latch are adjacent:

```
0x63907E66  bl   0x6394EC5C          ; variadic logger
0x63907E6A  bl   0x63907B74          ; latch 0x644B02B9 := 1
0x63907E6E  bl   0x63907B84          ; sticky 0x644B02BB := 1 (write-once)
```

The format string is `0x63F2B2E3`, dumped verbatim:

```
FW: Lost CAMOUT_V now[vfc:%d frc:%d], camoutv[vfc:%d frc:%d], sgc2[hq:0x%04x vq:0x%04x]
```

The latch helper is three instructions (bytes `02 4b / 01 22 / 7b 44 / 1a 70 / 70 47`), and its pool word was re-resolved in this session:

```
0x63907B74  ldr r3,[pc,#8]     ; pool 0x63907B80 = 0x00BA873D
0x63907B78  add r3,pc          ; PC 0x63907B7C + 0x00BA873D = 0x644B02B9
0x63907B7A  movs r2,#1
0x63907B7C  strb r2,[r3]
```

The sticky helper `0x63907B84` resolves the same way through pool `0x63907B94 = 0x00BA8731` to `0x644B02BB`. Both were verified byte-for-byte by the coordinator, not taken from a subagent report.

### 2.2 The emit condition - OBSERVED

```
0x63907E02  ldr.w  r0,[pc,#0x5b4]   ; pool 0x639083B8 = 0x00A5FB9E
0x63907E06  add    r0,pc            ; 0x63907E08 + 0x00A5FB9E = 0x643679A8
0x63907E08  ldr    r0,[r0]          ; *(0x643679A8)
0x63907E0A  adds   r0,#1
0x63907E0C  bl     0x63D0F56C       ; bic r0,r0,#0xff000000
0x63907E10  ldr    r3,[r4,#4]       ; p
0x63907E12  ldr    r3,[r3]          ; p[0]
0x63907E14  cmp    r0,r3            ; (baseline+1)&0xFFFFFF  vs  p[0]
0x63907E16  bne    0x63907E72
0x63907E18  ldr.w  r3,[pc,#0x5a0]   ; -> 0x643679DC
0x63907E1C  add    r3,pc
0x63907E1E  ldr    r3,[r3]          ; committed vfc snapshot
0x63907E20  cmp    r3,r0
0x63907E22  beq    0x63907E72
0x63907E24  ldr    r3,[r4,#8]       ; imaging object 0x643691FC
0x63907E26  ldrb.w r1,[r3,#0x514]
0x63907E2A  cmp    r1,#0
0x63907E2C  beq.w  0x63908394
0x63907E30  b      0x63907E72
```

The report is emitted only when all of these hold:

1. `p[0] == ((*(0x643679A8) + 1) & 0x00FFFFFF)`;
2. `*(0x643679DC) != p[0]`;
3. `imaging->byte[0x514] == 0` **and** the tail block `0x63908394` confirms it. That block stores zero into two stack slots, calls `0x63901EE8` (which copies two globals into those slots), reloads `[sp+0x24]`, and does `0x639083A6 cmp r0,#0` / `0x639083A8 bne.w 0x63907E32`. So the log path is taken only when `[0x514] == 0` and the helper's global is non-zero; otherwise the function continues at `0x63907E40` for the `rcv_ovf`/`sdvga_ovf` checks.

Note the control flow: when `[0x514] != 0` the branch at `0x63907E30` skips the log entirely, so `Lost CAMOUT_V` is reported only in the state where the imaging object says the CAMOUT output path is *not* externally gated.

`0x643679A8` is written only from `p[0]` on the CamCore start path (`0x63907B38`, sole caller `0x63913902`); `0x643679DC` is written only from `p[1]` on the accepted-frame commit path (`0x63907B40`, sole caller `0x63930414` inside `0x639303D0`). `p = *(0x643691FC+4)` and is never materialised statically.

**Reading (INFERRED, consistent with the `E0696` trace):** the check is a *commit-consistency watchdog*. It compares the live CAMOUT vertical counter against the value expected one frame after CamCore start, and against the last committed snapshot. It reports when the live counter has advanced exactly once since start while the committed snapshot still disagrees - i.e. the CAMOUT vertical stream stopped being committed. The `8`-tick cadence in `E0696` is the *period of the failing state*, not a programmed threshold: the whole function contains no `#8` constant, and the only count comparisons in the latch lifecycle are `cmp r3,#0xa` (10 refreshes) and `cmp r0,#0x14` (20 quiet cycles).

### 2.3 It is not a height compare - OBSERVED

- The immediate set of `0x63907D00`-`0x639083AE` is `0x30, 0x138, 0x144, 0..6, 0x54, 0x58, 0x1A, 0xA, 0x28, 0x2C, 0x90, 0xD0, 0x11, 0x5D4, 0xA0, 0x68A, 0x34, 0x38, 0x1C, 0xCE, 0x680, 0x3A3, 0x100, 0x56, 0x139, 0x13C, 0x13D, 0x14B, 0x10F, 0x129`. None is a height.
- A whole-image immediate sweep (624,570 32-bit Thumb candidates decoded, plus all 16-bit `CMP` imm8 forms) found **no `CMP`/`CMP.W`/compare against 3376, 3392, 3396, 3400, 3416, 3442, 3445, 3447, 3456, or 3480**. The only true video-height comparison in the image is `cmp.w rX,#0x870` (2160) at `0x63A2AE86`, `0x63A2AEAC`, `0x63A59680`, `0x63D67224` - the 4K *output* height.
- The apparent `#0xd72`/`#0xd98` immediates are logger line numbers: `0x636C868E movw r3,#0xd72` and `0x636C8800 movw r3,#0xd98`, each followed by `str r3,[sp]` and `bl <logger>` in `ModuleOnePassCcnrEncode.cpp`. The apparent `#0xd30`/`#0xd40` uses are structure member offsets (`add.w rX,r4,#0xd40`), each followed by a pointer store.
- `Lost CAMOUT_V` therefore fires because a *programmed pipeline geometry* stops producing committed vertical frames. The geometry dependence is indirect, which is why no static compare exists.

### 2.4 The consequence chain - OBSERVED

`0x644B02B9` is read at `0x63907838` inside the S-Ele refresh body `0x639077DC`. The only increment of `refresh_counter` `0x64367AF0` is `0x639078C4`, and the recovery path is:

```
0x63907856  ldr.w  r3,[r8]
0x6390785A  cmp    r3,#0xa
0x6390785C  bls    0x63907872
0x63907866  str.w  r7,[r8]          ; reset counter
0x63907870  str    r7,[r7]          ; r7 == 0 -> deliberate fault (reboot PC)
0x639078C4  adds   r2,#1
0x639078C6  str    r2,[r3]
```

So `refresh_cnt = 27` under `plus24` means 27 consecutive recovery/restart cycles. That is the low-FPS state, and it is a *symptom* of the watchdog, not the geometry limit.

## 3. The live matrix, re-derived in the coordinator session

The plan's Two-Experiment Filter names `shrink`, `test1`, and `plus24`. `test4` is also a recorded failure (`E0696`), so it was added as a fourth constraint. The table below was computed by applying `build_patch_plan` to the stock image and re-reading the patched values (script `budget.py`, deleted):

| Case | `dy` FE/RCV | `dy` timing | FE max vertical halfword (`+0x190`) | `rcv_b_vtot` | sensor `Y_SIZE` | timing `v` | period `2v` | `period - FE_max` | Live |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| stock | 0 | 0 | 3445 | 3447 | 3396 | 1740 | 3480 | 35 | PASS |
| `shrink` | -160 | 0 | 3285 | 3287 | 3236 | 1740 | 3480 | 195 | PASS, `refresh_cnt = 0` |
| `test1` | 0 | 0 | 3445 | 3447 | 4020 | 1740 | 3480 | 35 | PASS |
| `timing4` | 0 | +160 | 3445 | 3447 | 3396 | 1820 | 3640 | 195 | PASS |
| `plus24` | +24 | 0 | 3469 | 3471 | 3420 | 1740 | 3480 | 11 | FAIL, `Lost CAMOUT_V` |
| `test4` | +160 | +160 | 3605 | 3607 | 3556 | 1820 | 3640 | 35 | FAIL, `Lost CAMOUT_V` |

Two structural facts fall out of the same computation:

- **`FE_max - sensor_Y_SIZE = 49` in every case where both sides move together** (`shrink`, `plus24`, `test4`). The FE window always extends 49 lines past the sensor's active window, so the FE always consumes 49 lines of vertical blanking. No inequality of the form "FE asks for more lines than the sensor emits" separates `plus24` from stock or `shrink`.
- **`test4` has the same 35-line margin as stock and still fails.** Therefore the `3480`/`2 x v` line-budget theory is *refuted*: a comfortable margin does not rescue an upward move, and `timing4` proves that a large margin with stock FE is harmless. What separates the cases is not the margin but the **direction of the FE/RCV move**.

**Direction rule (OBSERVED from the four live cases):** every upward move of the FE/RCV vertical set (`+24`, `+160`) loses `CAMOUT_V`; an equal-magnitude downward move (`-160`) does not; sensor-only (`test1`) and timing-only (`timing4`) moves do not. The ceiling is therefore the **stock FE/RCV geometry itself**, enforced by a consumer that the 158-entry patch never touches.

## 4. `IMX284` is exonerated

- The 125-row `reglist` at `0x6454C484` (stride `0x14` = `u32 dest` + 16 value bytes, terminator `0x6454CE48`) is a movie/live readout programme. Columns 13-15 are zero in all 125 rows. Mode 16 and mode 17 both select column 11 (`0x63F109E1`), and **column 11 differs from column 10 in exactly four rows: 10-13, i.e. sensor registers `0x000A`-`0x000D`**. Byte-verified in this session:

  ```
  row 10 dest=0x645A1FE8 cols=... col10=8a col11=56   (0x000A Y_START lo)
  row 11 dest=0x645A1ED0 cols=... col10=02 col11=01   (0x000B Y_START hi)
  row 12 dest=0x645A1DAC cols=... col10=dc col11=44   (0x000C Y_SIZE lo)
  row 13 dest=0x645A1F24 cols=... col10=0a col11=0d   (0x000D Y_SIZE hi)
  ```

- No register in the whole sensor programme encodes a line count, `VMAX`, digital-crop height, MIPI `Y_OUT_SIZE`, EBD position, or SLVS packet count. The 68 staging records not referenced by the `reglist`, and the 16-entry auxiliary pointer array at `0x6454C438`, hold only TG/PLL-class values, every initial value `<= 0x0140`.
- The applicator `0x6379D618` is a bare `strb` per row (`0x6379D630 strb r5,[r4]`); there is no mask, clamp, or computed line count.
- Stock column 11 already places the window bottom at `342 + 3396 = 3738`, which is above `3480`. `test1` writes `Y_SIZE = 4020` (bottom 4050) through the same four bytes and passed.
- `3480` is therefore **not** a sensor `VMAX`. It is exactly `2 x 1740`, the host vertical period from `timing_rom_ntsc[16]` (`0x63E2598C`: `h=862`, `v=1740`) and `[17]` (`0x63E259A4`: `h=827`, `v=1740`). The 2:1 reading is corroborated by stock col 10 (`Y_SIZE 2780` against `2 x 1430 = 2860`).

Patch byte-diff receipts (coordinator, `verify_imx.py`, deleted): `putback` = 0 differing bytes; `test1` = 4 bytes, all in the `reglist`; `shrink` = 275 bytes; `plus24` = 142 bytes (2 sensor + 140 host). The planned write count is 152 (`--verify`), and `158` table entries include the 6 runtime `rt_t14` targets.

## 5. FE record layout: the "unpatched height" premise is refuted

### 5.1 Selector - OBSERVED

`0x63771C78` is a leaf selector `record = f(key=[r0], sub=[r0+8])` over a 16-record table at `0x63E12F14` with stride `0x540`. Case `key = 0x1F, sub = 0x0A` returns `0x63E14E94` (`fe_rec_0a`, index 6); `key = 0x1F, sub = 0x19` returns `0x63E15E54` (`fe_rec_19`, index 9); `key = 0x89` maps the same two records. All 13 direct caller sites were resolved; the selector, the two labels `0x63771D78`/`0x63771DE4`, and the records agree with `E0697` section 4.

### 5.2 The record is a list of 20-byte crop rectangles - OBSERVED

`0x63CCF5A2` expands three halfword pairs from `rec+0`, then `+4`/`+0xC`, and `0x63CCF5C6` expands `rec+0x00, +0x14, +0x28, +0x3C, +0x50, +0x6C, +0x80, +0x94, +0xA8, +0xBC, +0xD0, +0xD4, +0xD8, +0xF4, +0xFC` into a descriptor object at `+0x438...+0x658`.

The **first** halfword of every pair is a *horizontal* count, not a height. Three exact 2:1 matches against `rcv_desc` prove it:

| FE field | Value | `rcv_desc` counterpart | Value |
| --- | --- | --- | --- |
| `fe_rec_0a +0x014` | 3016 | `0x63E19752` | 6032 = 2 x 3016 |
| `fe_rec_0a +0x264` | 3012 | `0x63E197AE` | 6024 = 2 x 3012 |
| (mode width) | 3000 | `0x63E1979C` | 6000 |

`0x63CCBFD0` then folds the delta `(3168 + 2856 - 3096) = 2928` into the *horizontal* outputs (`out+0`, `out+0x10`, `out+0x20`), while the vertical outputs (`out+4`, `out+0x14`, `out+0x24`) take the line counts verbatim.

**Consequence:** the plan's premise that `+0x028 = 3096`, `+0x252/+0x256 = 1506`, `+0x264 = 3012`, `+0x2C8 = 868 vs 15100`, and `+0x2CE = 1668` are unpatched *heights* is **refuted**. `+0x028`, `+0x252`, and `+0x264` are horizontal. `+0x2C8`/`+0x2CE` are mode-specific scalars that do not vary with `dy`; they cannot distinguish `+24` from `-160`. `+0x1E4`/`+0x1EC`/`+0x250`/`+0x254`/`+0x20E` are already in the patch set.

### 5.3 The 66-offset set covers every vertical halfword - OBSERVED

Enumerating all 66 planned offsets of both records and reading the stock values, the vertical values present are `3376, 3391, 3392, 3394, 3398, 3401, 3407, 3441, 3442, 3443, 3445` (plus the scaled `5818` and the halved `1711/1695/848`). Every unpatched non-zero halfword in `fe_rec_0a`/`fe_rec_19` is horizontal (`3008, 3012, 3016, 3060, 3096, 3168, 3180, 3196, 3232, 4752`) or a delta/mask/sentinel (`32/36/48/50/58, 0x3FFA, 0x3FFC, mode masks`). **No vertical field is left at a stock value in either FE record.**

### 5.4 No `0xF300xxxx`/`0xF301xxxx` store on the FE path - OBSERVED (negative, bounded)

The FE consumer module `0x63CC0000-0x63CE8000` contains no resolved literal `>= 0xE0000000` at all. The only device base on the record chain is `0xF3120000`, loaded at `0x63768DBA/CD/DC/F0` and passed to `0x63CD463C`/`0x63CD4600`. The `0xF300xxxx`/`0xF301xxxx` windows are used only by read/log and display modules (`0x63765A0C`, `0x63857E54`, `0x63B46E52`, `0x63BC4D9C`, `0x63B09D6C`, `0x63A3CCB4`, `0x6395F904`). Limit: a *computed* `0xF3xxxxxx + field` address inside `0x63CD3xxx/0x63CD4xxx` is not excluded by a literal sweep.

## 6. `rcv_desc` - structure observed, reader unreachable statically

The block is one `0xC0`-byte unit at `0x63E19720` (the next unit starts at `0x63E197E0` with the same `1508` at `+0x08`). Non-zero halfwords include the six patched fields plus their horizontal partners:

```
+0x032 6032   +0x034 3396   +0x036 52    +0x03A 6032   +0x03C 48
+0x048 3392   +0x072 6048   +0x074 3396   +0x076 10     +0x078 3376
+0x07A 16     +0x07C 6000   +0x08A 3392   +0x08C 4      +0x08E 6024
+0x090 3447   +0x0B8 4095
```

Three independent searches found **zero** references to `0x63E19720` or any of its fields: no PC-relative materialisation, no absolute `u32` pointer, no resolved literal. It is reached only as `unknown_base + index`. This is an open boundary, and it means the six `rcv_desc` writes in the 158-entry table have **no proven consumer**.

## 7. `size_rows`, `rt_t14`, and the runtime-registered graph

- `size_rows` is a 19-entry array, base `0x63E24F70`, stride `0x4C` (76 bytes), ending at `drive_keys[19]` `0x63E25514`. `size_rows[16] = 0x63E25430` and `[17] = 0x63E2547C` are byte-identical.
- Fields of interest: `+0x1C = 6000` (horizontal active), `+0x24 = 20` (extra lines), `+0x3C = 3376` (`V_ACTIVE`, the only patched word). No field is a byte stride, a DMA split line, an IRQ line, or a `3392/3396/3442/3447/3480` value.
- The only static consumer of `size_rows` is `0x63CF0018`, which copies 18 words + 1 byte verbatim into `rt_t14` and takes `H1/V1`/`H2/V2` from the timing row. It contains **no arithmetic, compare, clamp, or multiply**. The bounded Unicorn verifier confirms the end-to-end effect: `--verify` asserts `rt_t14+0x50 == V_ACTIVE + dy` for modes 16 and 17 in all seven steps, so the patched `V_ACTIVE` does reach the runtime copy.
- `rt_t14` `0x6420128C`, `rt_t10` `0x64200EFC`, and the mode object `0x64200A48` each occur **exactly once** in the whole image - as slots of the global-pointer table at `0x645C1798` (`+0xAC`, `+0x5E10`). The object is registered with `0x63AC209C` and `0x63CEF540`. Section 8.6 records the stronger result: no code in this image references any of the four `rt_*` objects at all, so they have no static consumer here, not merely a runtime-registered one.
- The height-keyed tables near `0x6454AB60` (`3360, 3368, 3372, 3376, 3380, 3384, 3388, 3392, 3336, 3472, 3476, 3480, 3484, 3488`, ...) have **no static reader**. `0x63E48EA0`, `0x63E4AC80`, `0x63E58000`, and `0x63E87A50` decode as source-location metadata (`file id`, ascending line number) or a `0xF30037xx`/`0xF31037xx` register-write list; none is a vertical threshold with a static consumer.
- The three CamCore latch callers were re-verified and named: `0x63911EAC` = `FW: CamHead capParamSet Error 0x%x` / `FW: CamCore ParamSetError 0x%x`; `0x6391383E` = `FW: CamCore Start Error 0x%x`; `0x63914094` = `FW: Err:SetOutputFrame[0x%x]` / `FW: CamCore 2ndParamSetError 0x%x`. Each is a reaction to a non-zero return, not a height compare, and none is reachable by a static `3392`/`3447` test.

## 8. SGC1/SGC2/SGC3 programming map, `hq`/`vq`, and the timing-table negative

### 8.1 Aperture and source of `hvcyc` - OBSERVED

Each SGC channel owns a struct whose `+0x04` is the MMIO unit base. The two instances are `0xF3120000` (A) and `0xF3121000` (B, the `+0x1000` twins): `0x6376ADC0` is called only from `0x6376A428` with `r1 = 0xF3120000`, and the same setup function holds `0xF3121000` for the parallel struct at `ctx+0x920`. Within a unit, **SGC1 = `+0x200`, SGC2 = `+0x300`, SGC3 = `+0x400`**: `0x63CD46F4` writes `[base+0x220]`, and the debug printer `0x63765A3C` equates that word with `0xF3120200+0x20`.

The vertical cycle is written by exactly one instruction:

```
0x63CD46F8  str  ...  ->  0xF3120220 = ((H-1) << 16) | ((V >> 1) - 1)
```

with `r2 = [r4+0x34] = H-1` and `r1 = [r4+0x38] = (V>>1)-1`, sourced from the SGC1 parameter block `+0x38` (H) and `+0x3C` (V). **Correction to `E0697` section 3:** those two block words are filled from the imaging object at `+0x98` (H) and `+0x9C` (V) by `0x638B8CE8`, not from `0x643423B8+0x82C/+0x830`. The `+0x830` word lands at block `+0x1C8` (`0x638B8E44 ldr.w r3,[r5,#0x830]; str.w r3,[r4,#0x1C8]`) and **has no SGC consumer** in the traced code; `+0x82C` has no reader at all. This is consistent with the live trace: `SGC1` `H/V` tracked to `(824, 1820)` under `test4`, because it is reprogrammed from the runtime H/V rather than from a latched table.

### 8.2 The start function is event-driven, not per-frame - OBSERVED

`0x6376A9E0` caches five inputs in the channel struct (`+0x20` mode, `+0x2C`, `+0x30`, `+0x34 = H-1`, `+0x38 = (V>>1)-1`) and at `0x6376AB00-0x6376AB22` compares all five with the incoming values. **When all five match it branches to `0x6376AD54` and returns 0 without writing a single register.** Only a change runs the `0x220 / 0x224 / 0x248 / 0x264 / 0x26C / 0x210 / 0x258 / 0x208` sequence. `hvcyc`, `H-101`, and the window registers are therefore event-driven. Nothing in the SGC1 timing set is rewritten per frame.

### 8.3 `hq` and `vq` are diagnostic only - OBSERVED

The log site is `0x63907E4E` (32-bit `ldr.w`/`add pc` to `0x63F2B2E3`). The sequence is `0x63907E32 bl 0x63CCF2C8` -> `r0 = [chan+4]` (MMIO base `0xF3120000`), `0x63907E3C bl 0x63CD49EC`, which strokes `0xF3120314` (`str 1`), polls it until clear, then tail-calls `0x63CD474A`:

```
0x63CD474A  r0 = [base+0x318]
            [r1] = r0 & 0x3FFF
            [r2] = (r0 >> 16) & 0x3FFF
```

so `vq = bits[13:0]` and `hq = bits[29:16]` of **`0xF3120318`**, the SGC2 `hqvq` word. `0x63CD49EC` has exactly one caller in the image (`0x63907E3C`), so `hq/vq` feed only this log string and do not participate in the pass/fail decision.

### 8.4 The remaining window registers and one unresolved producer - OBSERVED

Two SGC1 registers are genuine H/V *windows* rather than counts:

| MMIO | Writer | Value |
| --- | --- | --- |
| `0xF3120264` | `0x63CD5678` | `(((H-1) + sxtb(block[0x40])) << 16) | 1` when `block[0x41] != 0` and `block[0x42] != 0`, else `0` |
| `0xF312026C` | `0x63CD5688` | `(block[0x42] << 16) | block[0x41]` |

`0x638B8CE8` writes block `+0x40` from `imaging+0xA0` but writes **neither `+0x41` nor `+0x42`**; the only other writer of the block is the `memcpy` at `0x638B6124`. Both bytes are read at `0x638B6894` and forwarded into the two registers above. Their producer is therefore unidentified, and they are the one remaining place where a stock-valued window byte could persist while the FE/RCV heights move.

### 8.5 SGC2 measurement limits are a closed loop, and the 8-tick cadence matches its decimation - OBSERVED + INFERRED

`0xF3120318`, `0xF312031C`, and `0xF3120338` have **no software writer** in either swept bank (`0x63CC0000-0x63CE8000`, `0x63760000-0x6376C000`); the low 14-bit halves are hardware-produced. The high halves at `0xF312032C`, `0xF3120330`, `0xF3120334`, `0xF312033C`, and `0xF312035C` are written by `0x6376910C` as `measured + trim`, so the limit block is re-derived around the *measured* H/V. That function contains the only `/8` decimations in the traced path (`0x637691D6 movs r1,#8`, `0x63769212 movs r1,#8`, rounding at `0x63CCFA54`). **INFERRED:** the `E0696` "every 8 `vfc` ticks" cadence is the trim re-evaluation period of this loop, not a counter wraparound, which explains a *periodic* re-detection of a *persistent* geometry error.

### 8.6 `rt_t10`/`rt_t14`/`rt_t18`/`rt_t1c` have no consumer in this image - OBSERVED (negative, bounded)

Each object occurs exactly once, as a slot of the pointer table at `0x645C1798`:

| object | VA | slot VA |
| --- | --- | --- |
| `rt_t14` | `0x6420128C` | `0x645C1844` |
| `rt_t1c` | `0x6442F0C8` | `0x645C405C` |
| `rt_t10` | `0x64200EFC` | `0x645C4754` |
| `rt_t18` | `0x64201B74` | `0x645C8DC0` |

Searches: ADR T1 at every even offset, `ldr`+`add pc` (16-bit and 32-bit forms), `movw`/`movt` pairs, and 32-bit literals equal to each slot offset. None produced a genuine reference; the literal hits were code halfwords (`0x00AC` = `lsls r4,r5,#2`, `0x7628` = `strb r0,[r5,#0x18]`, `0x28C4` = `cmp r0,#0xc4`, `0x2FBC` = `cmp r7,#0xbc`). By contrast the same method finds the imaging object slot `0x3D78` sixteen times at `0x638C04B8-0x638C080C`. The complete data-source set of the SGC timing writers is the channel struct, the SGC1 parameter block, the imaging descriptor bytes (`+0x98`, `+0xA0`, `+0x608`, `+0x640`), and the runtime clock read `0x63764434`. **No path from `rt_t10`/`rt_t14`/`rt_t18`/`rt_t1c` to an SGC register exists in this image.** A consumer outside this image or behind a computed GOT index is not excluded.

## 9. Rejected hypotheses

| # | Hypothesis | Verdict |
| --- | --- | --- |
| 1 | DMM pool too small | **Refuted** by `E0697` section 5; the rule-5 slot holds 6000x4000x2 and `plus24` needs less. |
| 2 | An unpatched FE height (`+0x028`, `+0x252`, `+0x264`, `+0x2C8`, `+0x2CE`) is the ceiling | **Refuted**: those fields are horizontal or `dy`-independent (section 5.2), and stock already runs with vertical `3445` next to horizontal `3096`. |
| 3 | `rcv_vtot = 3471` exceeds the `3480` frame budget | **Refuted** by `test4`: `3607` against `3640` leaves exactly the stock 35-line margin and still fails (section 3). |
| 4 | `SGC1` `H/V` stayed at the stock period | **Refuted live** (`E0696` section 4): `(824, 1820)` under `test4`. |
| 5 | IMX284 clamps readout at 3396 | **Refuted** by `test1` (`Y_SIZE = 4020` passes) and by the window bottom `3738` at stock (section 4). |
| 6 | Stale `rt_t10+0x12` or `rt_t18` | **Refuted**: `timing4` passed without rewriting them; `plus24` failed with them stock, which is not separable. |
| 7 | Missing lockstep timing patch | **Refuted** by `test4`: full lockstep `(824,1820)`/`(791,1820)` still loses `CAMOUT_V`. |
| 8 | `rcv_ovf` / `sdvga_ovf` | **Refuted** by the trace: both `0`; both only guard the two *other* log messages (`0x63907E84`, `0x63907EC2`). |
| 9 | A height compare inside the `Lost CAMOUT_V` detector | **Refuted** by full disassembly and the whole-image immediate sweep (section 2.3). |
| 10 | Sensor protocol truncation / masking | **Refuted**: the applicator is a bare `strb`, and `test1`'s `4020` needs 12 bits and arrives intact. |
| 11 | `hq`/`vq` carry the decision | **Refuted**: they are read from `0xF3120318` only at the log site, and `0x63CD49EC` has exactly one caller (`0x63907E3C`). |
| 12 | `rt_t10`/`rt_t14`/`rt_t18`/`rt_t1c` drive SGC timing | **Refuted for this image** (section 8.6): no reference of any kind, and the SGC writers' full source set excludes them. |
| 13 | `hvcyc` at `0xF3120220` latched from a stale table | **Refuted**: its only writer `0x63CD46F8` uses runtime H/V from block `+0x38/+0x3C` (section 8.1), and the start function skips all writes when its five inputs are unchanged (section 8.2). |

## 10. What remains: the exact open boundary

The image proves the *shape* of the failure but not the last hop:

1. **No static compare exists** against any of the vertical constants, so the violated limit is a value *read from hardware or from a runtime object*, not an immediate.
2. **The FE record is fully patched** (section 5.3), so the limit is not a leftover FE height.
3. **The `rcv_desc` block has no static reader** (section 6), so its six patched fields cannot be confirmed or cleared as the carrier.
4. **The SGC1 parameter block has two bytes with no identified producer**: `+0x41` and `+0x42`, forwarded into the window registers `0xF3120264`/`0xF312026C` (section 8.4). This is the only remaining SGC-side place where a stock-valued window could persist.
5. **`rt_t14`/`rt_t10`/`rt_t18`/`rt_t1c` have no consumer at all in this image** (section 8.6), so they are not candidates. The runtime-registered object graph that still matters is the SGC1 parameter block itself (`0x64339DF8`).

Three candidates survive; all are direction-dependent by construction, and none is separable from the others offline:

- **R1:** a consumer compares an FE/RCV vertical total against a **stock-valued copy** of the same total, so any `dy > 0` makes the difference negative (or the end edge exceed the stock bottom), while `dy < 0` only adds slack. The most likely carrier is the unread `rcv_desc` unit.
- **R2:** a buffer or descriptor sized at the **stock** geometry is overrun by `dy > 0` and under-filled by `dy < 0`, so the commit path never completes a vertical frame.
- **R3:** the SGC2 measurement window closes one-sidedly. The low 14 bits of `0xF3120318` and friends are hardware-measured, and `0x6376910C` re-derives the limits as `measured + trim` (section 8.5); a guard of the form `measured < programmed` would fail only when the measured vertical position grows, which is exactly `dy > 0`. The `/8` decimation in that function also explains the observed 8-tick cadence better than any counter wraparound.

`Lost CAMOUT_V` is the *report* in all three cases: `p[0]` stops matching `(baseline+1)` while the committed snapshot disagrees (section 2.2).

## 11. Recommended next live step (requires explicit user GO)

The single most discriminating read-only test is a **minimal upward bisect**, because it separates "the ceiling is exactly the stock geometry" (R1/R2/R3) from "the ceiling is a numeric wall somewhere above stock":

1. Apply `dy = +8` on the same 142 write sites (`Y_START = 338`, `Y_SIZE = 3404`, FE `+8`), keep timing stock, set `PA 0x0434FD7E |= 0x40` (`noreboot`), and watch `refresh_cnt` `0x04367AF0` and the latch `0x044B02B9` with the existing 50 ms sampler.
2. If `+8` already fails, the ceiling is the stock geometry itself, and no numeric wall exists above it. If `+8` passes, bisect `+16`.
3. In the same shot, read `rt_t14[16]+0x50` (`PA 0x04201A5C`) and `rt_t14[17]+0x50` (`PA 0x04201AD4`) to confirm the patched `V_ACTIVE` reached the runtime copy even though it has no consumer, and read `rcv_desc` `PA 0x03E19740`-`0x03E197B0` to see whether the patched values are latched there.
4. Read the SGC2 measurement window `0xF3120318`, `0xF312032C`, `0xF3120330`, and `0xF3120334` through a single decoded 4 KiB page access, and the SGC1 windows `0xF3120264`/`0xF312026C`. These are device-map pages, so under `E0647` each address needs its own per-address go-ahead and must pass `tools/devmem_policy.py`; they are not covered by the standing read-only grant.
5. Restore `putback`, `noreboot off`, `resetcnt`, and verify `158/158 STOCK` as in `E0696`.

A second, equally targeted experiment (write-gated, needs GO) is `plus24` **without** the six `rcv_desc` writes: if it still fails, the `rcv_desc` halfwords are inert; if it passes, `rcv_desc` is the carrier. This is the cleanest way to retire the section 6 open boundary.

## 12. Repeatability and receipts

- `C:\Python314\python.exe -m tools.research.a6400_open_gate --verify` - 7/7 steps `VERIFY PASS`, `putback=EXACT`: `putback`, `test1`, `test4`, `test3`, `test2`, `shrink`, `plus24`; `patches=152` each.
- `C:\Python314\python.exe -m unittest tests.test_a6400_open_gate -q` - `Ran 5 tests ... OK`.
- `C:\Python314\python.exe -m tools.research.knowledge_map check` - `knowledge map OK`.
- Coordinator byte checks: `reglist` rows 10-13, `putback/test1/shrink/plus24` diffs (0/4/275/142), latch pools `0x00BA873D`->`0x644B02B9` and `0x00BA8731`->`0x644B02BB`, detector pools `0x639083B8 = 0x00A5FB9E` / `0x639083BC = 0x00A5FBBC`, log strings `0x63F2B2E3`/`0x63F2B29B`/`0x63F2B2C0`.
- Scratch scripts lived under `.scratch_0698/` and were removed (moved out of the repository tree) before this record was completed; no tracked input, firmware byte, or hardware state was modified.
- `tools/research/a6400_open_gate.py` and `a6400_og_ram_table.h` were **not** modified: no write patch is justified by this record, and none is proposed as ready.

## 13. Superseded conclusions

- `E0697` section 7 and `C19` ("the missing patch may be the SGC H/V BSS words") are **retracted**: the `E0696` section 4 trace shows `SGC1` `H/V` tracking to `(824, 1820)` under `test4`, and `test4` still fails. The SGC period is not the ceiling.
- `E0697` section 8 asked which latch arms `S-Ele`; that gate is **closed**: it is `Lost CAMOUT_V` (`0x63907E6A`), with `rcv_ovf`/`sdvga_ovf` both `0`.
- The plan's section 2/1 premise that `+0x028 = 3096` and friends are unpatched FE heights is **refuted**; they are horizontal counts.
- `E0697` section 3 attributed the `hvcyc` source words to `0x643423B8+0x82C/+0x830`. That is **corrected** (section 8.1): `hvcyc` is packed from block `+0x38/+0x3C`, which `0x638B8CE8` fills from imaging `+0x98/+0x9C`. The `+0x830` word lands in block `+0x1C8` with no SGC consumer, and `+0x82C` has no reader.
- `E0697` section 3 treated `rt_t10+0x12` and `rt_t18` as possibly relevant. They are **retracted as candidates** (section 8.6): the four `rt_*` objects have no consumer of any kind in this image.
