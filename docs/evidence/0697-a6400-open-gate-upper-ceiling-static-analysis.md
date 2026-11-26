# E0697 — ILCE-6400 Open Gate upper ceiling: static ruling of S-Ele, SGC, FE/RCV, DMM, and IMX284

- **Date:** 2026-11-26
- **Scope:** Offline, read-only root-cause of why `dy > 0` raises `S-Ele refresh_cnt` while `dy = -160` does not. No camera, USB, or `/dev/mem` access.

## 1. Image and physical constraints

Image: `artifacts/hardware/av-cam-camera-original.bin`, 16,803,048 bytes, SHA-256 `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`. ORIL VA base `0x635C6000`. PA = VA − `0x60000000`. File offset = VA − `0x635C6000`.

`DmmConfig.bin`: `firmware/extracted_a6400/firmware.tar_unpacked/0800_appli/setting/DmmConfig.bin`, 77,108 bytes.

Physical matrix already recorded in E0696. It is a constraint, not retested here:

| Step | What changed | Result |
| --- | --- | --- |
| `test1` | reglist column 11 only, `Y_SIZE=4020` | PASS, smooth FPS |
| `timing4` | timing ROM + `rt_t14` only, 24p `(824,1820)`, 25p `(791,1820)` | PASS, smooth FPS |
| `shrink` | `dy=-160` on all 158 entries, timing stock | PASS, `refresh_cnt=0` |
| `plus24` | `dy=+24` on all 158 entries, timing stock, `rcv_vtot=3471` | FAIL, low FPS, `refresh_cnt=27` |
| `test4` | `dy=+160` plus the timing4 pair | FAIL, normal colors, `refresh_cnt=33` |

Reboot PC without debug bit `0x40` is `0x63907870`.

Tooling: `C:\Python314\python.exe`, Capstone 5.0.7, and WSL `arm-linux-gnueabi-objdump -b binary -m arm -M force-thumb`. Addresses below were checked against the image in this session, not taken only from a trace log.

## 2. S-Ele refresh

`0x639077DC` is the refresh function. The only increment of `refresh_counter` is:

```
0x639078BE: ldr r3, [pc, #120]  ; pool word 0x00A6022C
0x639078C0: add r3, pc          ; r3 = 0x64367AF0
0x639078C4: adds r2, #1
0x639078C6: str r2, [r3]
```

| Field | VA | PA | Role |
| --- | --- | --- | --- |
| `refresh_counter` | `0x64367AF0` | `0x04367AF0` | `u32`. Only `++` is `0x639078C4`. |
| side counter | `0x643679AC` | `0x043679AC` | Incremented on the unarmed path. Compared with 20. Does not increment the refresh counter. |
| refresh latch | `0x644B02B9` | `0x044B02B9` | `u8`. Nonzero arms the body. Cleared when the body runs. |
| sticky seen | `0x644B02BB` | `0x044B02BB` | Set with the latch. No clearer in this function. |
| RCV overflow flag | `0x644B02CA` | `0x044B02CA` | Logged. Does not increment `refresh_counter`. |
| SDVGA overflow flag | `0x644B02C9` | `0x044B02C9` | Logged. Does not increment `refresh_counter`. |
| debug byte | `0x6434FD7E` | `0x0434FD7E` | Bit `0x40` skips the fault store. Bit `0x02` skips the refresh body. |
| state byte | `0x6434D9AB` | `0x0434D9AB` | Bits `0x81` also arm the body. Bit 0 is cleared in the body. |
| camout vfc snap | `0x643679DC` | `0x043679DC` | Printed as camoutv `vfc`. |
| camout frc snap | `0x6436799C` | `0x0436799C` | Printed as camoutv `frc`. |

The body runs only when `r1==0`, a mode gate returns nonzero, bit `0x02` of the debug byte is clear, `0x63D4C566` returns 0, `[object+0x539]!=0`, and either the latch is nonzero or state bits `0x81` are set. There is no compare with 3392, 3442, 3447, 3480, or `V_ACTIVE` in `0x639077DC..0x63909B00`.

If bit `0x40` is clear and the counter is already greater than 10:

```
0x6390785A: cmp r3, #10
0x6390785C: bls 0x63907872
0x63907870: str r7, [r7]    ; r7 is 0. This is the reboot PC.
```

That store matches the E0696 reboot at `PC 0x63907870`. With bit `0x40` set, the counter can pass 10, which is how `plus24` reached 27.

The latch store `0x63907B74` is reached from Lost CAMOUT_V (`0x63907E6A`, string `0x63F2B2E7`) and from three CamCore error logs (`0x63911EAC`, `0x6391383E`, `0x63914094`). Lost CAMOUT is a `vfc` mismatch, not a line-count compare:

```
0x63907E14: cmp r0, r3
0x63907E16: bne 0x63907E72
0x63907E20: cmp r3, r0
0x63907E22: beq 0x63907E72
0x63907E2A: cmp r1, #0          ; [imaging+0x514]
0x63907E2C: beq.w 0x63908394    ; then 0x63907E32 or 0x63907E40, then the latch
```

`refresh_cnt=27` means `0x639078C4` ran 27 times. `refresh_cnt=0` means it did not run. The counter does not say which of the four latch sources fired. After the handler returns, the latch and both overflow flags are already clear. `0x644B02BB` stays set and does not identify the source.

`0x6394E4E4` sends the formatted line through UIPC thunk `0x635DD470`. In this file the callee slot is 0. There is no `kikilog` string and no retained text ring in `av-cam.bin`.

## 3. SGC1 / SGC2 and the timing tables

No `MOVW`/`CMP` immediate of 3392, 3442, 3447, 3480, 3376, 3396, 3416, or 3471 programs the SGC start path. `MOVW #0xD72` at `0x636C868E` and `MOVW #0xD98` at `0x636C8800` are logger line numbers.

`0x6379D2F8` binds `ImagerDrvParam`. Mode 16 and 17 share drive keys `0x1F` and `0x89` and the same column-11 timing geometry. Populators, called from `0x6379D500`:

| Table | VA | Stride | Filler |
| --- | --- | --- | --- |
| `rt_t10` | `0x64200EFC` | `0x30` | `0x63CEFF4E` |
| `rt_t14` | `0x6420128C` | `0x78` | `0x63CF0018` |
| `rt_t18` | `0x64201B74` | `0x34` | `0x635C8838` |
| `rt_t1c` | `0x6442F0C8` | `0x12` | `0x635C8A48` |

`rt_t10+0x12` is `round(h1 * 133 / 72)`. For stock 25p that is 1528, matching the E0696 preflight. Stock 24p computes 1592; E0696 read 1593. That one-count gap is not resolved here. `rt_t10+0x24` stays 24 while `h1==h2`. `timing4` changed `h` without rewriting `rt_t10` or `rt_t18` and passed, so those tables are not required for that timing step. `plus24` left timing stock, so `rt_t10+0x12` and `rt_t18` were still the stock-`(h,v)` values. They are not the `plus24` ceiling.

SGC1 parameter block: BSS `0x64339DF8` (zeros in the file). `0x638B6894` loads it (`pool 0x00A83560` added to PC). `H` is the word at `+0x38`, `V` is the word at `+0x3c`. `0x6376A9E0` programs MMIO:

```
hvcycle at 0xF3120220 = ((H - 1) << 16) | ((V >> 1) - 1)
```

Sibling blocks are `0xF3120300` (SGC2) and `0xF3120400` (SGC3), plus the `+0x1000` twins. The only `memcpy` of this block is `0x638B6124`, size `0x144`, called from `0x63932A6E`. The source is `[imaging+0x15C]`, and that sub-struct is filled by `0x638B8CE8` from a runtime object. The `H`/`V` words inside the copied window come from source offsets `+0x7EC` and `+0x7F0`, which land at global `0x643423B8 + 0x82C` and `+0x830` (`[0x645C5510] = 0x643423B8`). Those words are not among the 158 static patch entries. This pass did not find a store that copies `size_rows+0x3C` or the timing `v` into them, so it is not shown whether a mode re-entry rewrites `hvcycle` from the patched height.

## 4. FE / RCV

`0x63771D78` and `0x63771DE4` are case labels inside `0x63771C78`, not separate functions. Drive keys `0x1F` and `0x89` both return `fe_rec_0a` `0x63E14E94` for variant `0x0A` and `fe_rec_19` `0x63E15E54` for variant `0x19`. The two records match on the 66 patched vertical halfwords. `+0x2C8` differs (868 vs 15100).

Checked stock halfwords:

| Offset | `fe_rec_0a` | `fe_rec_19` | In the 66-offset patch? |
| --- | --- | --- | --- |
| `+0x000` | 3232 | 3232 | no |
| `+0x002` | 3442 | 3442 | yes |
| `+0x00A` | 3392 | 3392 | yes |
| `+0x028` | 3096 | 3096 | no |
| `+0x02A` | 3392 | 3392 | yes |
| `+0x20E` | 5818 | 5818 | yes, scaled |

`0x63CCC03E` copies two halfwords from its pointer. The 4K caller at `0x63764578` does `adds r0, #0x28` first, so it copies `+0x028` (3096, unpatched) and `+0x02A` (3392+dy), not record `+0x000`. Stock already runs with 3392 next to an unpatched 3096, so that pair is not a ceiling that stock would fail. No instruction in the selector compares 3232, 3392, 3442, 3447, or 3480.

`rcv` halfwords, base `0x63E19720`:

| VA | Stock |
| --- | --- |
| `0x63E19754` | 3396 |
| `0x63E19768` | 3392 |
| `0x63E19794` | 3396 |
| `0x63E19798` | 3376 |
| `0x63E1979C` | 6000 (`u32`) |
| `0x63E197AA` | 3392 |
| `0x63E197B0` | 3447 |

3416 is not a stock constant. It is 3392+24.

## 5. DMM

`0x63DDF414` in this image is not the canvas table (the words there are small signed values). E0578's address belonged to the other `av-cam.bin`. This image's canvas is `0x63DF199C`: seven records, the movie rows are 3840×2160, 2880×2160, and 3240×2160. There is no 6000×3376 row. `6000` (`0x1770`) and `3376` (`0xD30`) sit in the mode record at `0x63E25430`, which is the `size_rows` object the open-gate model already patches at `+0x3C`.

`DmmConfig.bin` contains no `u32` 6000, 3376, 3392, or 3442. The rule-5 slot size `0x2EF9200` (49,254,912) occurs in the file. Against it:

| Frame | Bytes | Slack in `0x2EF9200` |
| --- | --- | --- |
| 6000×3376×2 | 40,512,000 | 8,742,912 (728 lines) |
| 6000×4000×2 (`dy=+624` from 3376) | 48,000,000 | 1,254,912 |
| 6144×4000×2 | 49,152,000 | 102,912 |

`0xFD2000` is exactly 3840×2160×2, the encode canvas, already shorter than a stock 3376-line full-width frame. No DMM slot should be enlarged for `plus24`. A pool abort is also inconsistent with `test4`, which still produced normal-color pixels at low FPS.

## 6. IMX284

Mode bytes at `0x63F109E1`: index 16 = 11, index 17 = 11. `0x6379D618` stores that column byte with `strb` and does not mask it. Column 11 of rows 10–13 is the live window:

| Row | Value byte | Sensor register at pointer−2 | Column 11 | Stock window |
| --- | --- | --- | --- | --- |
| 10 | `0x645A1FE8` | `0x000A` | `0x56` | `Y_START` low |
| 11 | `0x645A1ED0` | `0x000B` | `0x01` | `Y_START` high, 342 |
| 12 | `0x645A1DAC` | `0x000C` | `0x44` | `Y_SIZE` low |
| 13 | `0x645A1F24` | `0x000D` | `0x0D` | `Y_SIZE` high, 3396 |

The halfwords `0x0025`, `0x001E`, `0x0027`, and `0x001B` are the next record's address, not the sensor register. `tools/research/a6400_open_gate.py` now labels `0x000A`–`0x000D`. The patched bytes are unchanged.

The only column-11 pair equal to 3396 is `0x000C/0x000D` itself. `0x000E/0x000F` column 11 is `0x1808` (6152). Rows 24–27 (`0x001D`–`0x0020`) are zero in all 16 columns. `0x6379F00C` writes one shutter word into both `0x001D/0x001E` and `0x001F/0x0020`. It does not read `Y_SIZE` and it does not write 1740. `test1` therefore puts `Y_SIZE=4020` on the wire. A sensor clamp at 3396 is not in this register program.

## 7. Synthesis

Ruled out as the `plus24` ceiling, each one inconsistent with the physical matrix or absent from the image:

- DMM pool size. The large rule-5 slot still holds `dy=+624`.
- An FE or RCV compare with 3392, 3442, 3447, or 3480.
- An SGC1/SGC2 compare with those same constants.
- Stale `rt_t10+0x12` or `rt_t18`. Timing was stock for `plus24`, and `timing4` passed without updating them.
- IMX284 holding readout at 3396. `test1` sends `Y_SIZE=4020` and stays smooth.
- `rcv_vtot` versus 3480. 3471 is already below 3480, and no instruction compares them.

What the image does show: `refresh_cnt` counts entries to the S-Ele body, and that body is armed by a `vfc` mismatch (Lost CAMOUT_V) or a CamCore parameter error. The step that produces the mismatch is the upward change of the 66 FE halfwords, the two `V_ACTIVE` words, and the six `rcv` halfwords. Shrinking those fields stays at `refresh_cnt=0`. Changing only the sensor window or only the timing pair also stays smooth.

The vertical period that SGC writes to `0xF3120220` comes from BSS `0x64339E30` / `0x64339E34` (`0x64339DF8+0x38/+0x3C`). Those words are outside the 158-entry patch. Static analysis does not prove they stay at the stock period across a mode re-entry, and it does not prove which of the four latch sources fired. No write patch is justified from this record.

## 8. Next single-shot live read

Read only, one page, during a later authorized `plus24` attempt, before the refresh body clears the flags. Do not write these addresses in that shot.

| PA | What it distinguishes |
| --- | --- |
| `0x044B02B9` | Latch still 1 means the refresh body has not consumed it yet. |
| `0x044B02BB` | Sticky. Stays 1 after the first arm and does not name the source. |
| `0x044B02CA` / `0x044B02C9` | RCV overflow versus SDVGA overflow. Either can be set without incrementing `refresh_cnt`. |
| `0x0434D9AB` | State bits `0x81` arm the body even if the latch is clear. |
| `0x043679DC` / `0x0436799C` | Camout `vfc` / `frc` snapshot printed by Lost CAMOUT_V. |
| `0x04339E30` / `0x04339E34` | SGC `H` and `V` that become `hvcycle` at `0xF3120220`. |
| `0x04367AF0` | How many refresh bodies have completed. Already known to reach 27 on `plus24`. |

If `H`/`V` are still the stock period while `V_ACTIVE` is 3400, the missing patch is those two BSS words (and the MMIO word they program), not a DMM slot. If they already track 3400 and the latch still sets, the mismatch is elsewhere on the Lost CAMOUT path and a height write will not fix it.
