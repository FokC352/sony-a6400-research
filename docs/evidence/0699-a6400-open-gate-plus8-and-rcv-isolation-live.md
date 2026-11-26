# E0699 - ILCE-6400 Open Gate `plus8` Bisect, `rcv_desc` Isolation (`plus24_norcv` vs `plus24_rcvonly`), and Live `sgc1_win` Capture

- **Date:** 2026-11-26
- **Scope:** Live read/RAM isolation on the physical ILCE-6400 (`158`-entry `a6400_og_ram` helper with `noreboot` bit `PA 0x0434FD7E |= 0x40`) answering the `E0698` section 11 decisions (`plus8` bisect, `rcv_desc` A/B split, and live `sgc1_win` `+0x40..+0x42` read via verified RAM page `0x04339000` without touching device-map `0xF3120000`). Camera restored to verified `STOCK` (`158/158`, `noreboot off`, `/tmp_bt` cleaned).
- **Predecessors:** `E0695` (offline port), `E0696` (live `test1`/`timing4`/`shrink`/`plus24`/`test4` + `watch`), `E0697` (static ceiling sweep), `E0698` (`Lost CAMOUT_V` watchdog analysis + `R1`/`R2`/`R3` + `sgc1_win`).

## 1. Inputs, Tooling Identity, and Offline Verification

| Item | Value |
| --- | --- |
| Reference firmware | `artifacts/hardware/av-cam-camera-original.bin` (`16,803,048` B, SHA-256 `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`, ORIL VA `0x635C6000`) |
| Planner & table | `tools/research/a6400_open_gate.py`, `tools/research/a6400_og_ram_table.h` (`N_OG_PATCHES = 158`) |
| Target ARM helper | `artifacts/tests/a6400_og_ram` (built via WSL `arm-linux-gnueabi-gcc -O2 -march=armv7-a -static -Wall -Wextra`, `625,384` B, SHA-256 `3cc32f67edf7663917ae90771f9546890b2410e19edb41f3d3768fbe3fa459fe`, `readback_verified: true`) |
| Added helper modes | `plus8` (`dy = +8`), `plus24_norcv` (`OG_PATCHES[146..151]` kept stock), `plus24_rcvonly` (`reglist[0..3] + rcv_desc[146..151]` `+24`, `FE` & `size_rows` stock), plus `sgc1_win` (`PA 0x04339E38..0x04339E3A`) & `rcv_desc` printout in `diag`/`watch` |

Baseline verification on the camera in MSC prior to any write:
- `usb-state`: `uptime_s=3450`, `transport=usb_extcmd`, `modules.custom=none`.
- `/tmp_bt/a6400_og_ram dump`: `LIRO_HDR: MATCH @ 0x035c6000`, `COUNTS: total=158 stock=158 ... other=0`, `VERDICT: STOCK`.
- `/tmp_bt/a6400_og_ram diag`:
  - `refresh_cnt=0 side_cnt=0 latch=0x00 sticky=0x00 rcv_ovf=0x00 sdvga_ovf=0x00`
  - `debug_flag=0x00 state_byte=0x00 camout_vfc=177 camout_frc=6872238`
  - `sgc1_HV=(858,1750) sgc1_win=(+0x40=0x00,+0x41=0x00,+0x42=0x00)`
  - `rcv_desc=[0x03e19754:3396 0x03e19768:3392 0x03e19794:3396 0x03e19798:3376 0x03e197aa:3392 0x03e197b0:3447]`
  - `rt_t14[16/17]` `V_ACTIVE=3376`, `hv1=hv2=(862,1740)/(827,1740)`.

## 2. Test 1 - Minimal Upward Bisect (`plus8`, `dy = +8`)

- **Command / Policy:**
  `/tmp_bt/a6400_og_ram noreboot on && /tmp_bt/a6400_og_ram resetcnt && /tmp_bt/a6400_og_ram apply plus8`
  (`APPLY plus8: ok written=144 unchanged=14`; `Y=(338,3404)`, `FE=(3450, 3400, 5832)`, `rcv_desc=[3404, 3400, 3404, 3384, 3400, 3455]`, `rt_t14 V_ACTIVE=3384`, timing stock `(862,1740)/(827,1740)`).
- **Action:** User switched camera from MSC -> Movie 4K 24p (~5 s) -> MSC.
- **Observation (`diag`, `dump`, `alog`, `/proc/uptime`):**
  - `uptime = 4106.33` s (monotonic, no reboot; `debug_flag = 0x40`).
  - `VERDICT: PLUS8` (`plus8=158/158`).
  - `DIAG: refresh_cnt=39 side_cnt=2 latch=0x00 sticky=0x01 rcv_ovf=0x00 sdvga_ovf=0x00`
  - `camout_vfc=43527 camout_frc=1814928488`
  - `ALOG_MATCHED: 0` (1 line `EVENTS`; no S-Ele crash because `noreboot` bit `0x40` was set).
- **Conclusion (OBSERVED):** Even the smallest Bayer-aligned upward step (`dy = +8`, `FE = 3400`, `rcv_b_vtot = 3455`) **fails** identically to `plus24` (`refresh_cnt = 39`, `sticky = 0x01`, `rcv_ovf = 0`, `sdvga_ovf = 0`). There is **no numeric wall above stock**: the ceiling is strictly the stock geometry itself.

## 3. Test 2 - `rcv_desc` Isolation (`plus24_norcv` vs `plus24_rcvonly`)

| Step | What moved (`+24`) | What stayed `STOCK` | `APPLY` receipt | Live Movie 4K 24p result |
| --- | --- | --- | --- | --- |
| `plus24_norcv` | `reglist[0..3]` (`Y_SIZE=3420`), `fe_rec_0a/19` (66 offsets each), `size_rows[16,17]+0x3c`, `rt_t14+0x50` (`V_ACTIVE=3400`) | 6 `rcv_desc` (`[3396, 3392, 3396, 3376, 3392, 3447]`), `timing` | `written=138 unchanged=20` | **FAIL:** `uptime=4338.24` s, **`refresh_cnt=44`**, `side_cnt=2`, `sticky=0x01`, `rcv_ovf=0`, `sdvga_ovf=0`, `camout_vfc=55323` |
| `plus24_rcvonly` | `reglist[0..3]` (`Y_SIZE=3420`), 6 `rcv_desc` (`[3420, 3416, 3420, 3400, 3416, 3471]`) | `fe_rec_0a/19`, `size_rows[16,17]+0x3c`, `rt_t14+0x50` (`V_ACTIVE=3376`), `timing` | `written=8 unchanged=150` | **PASS:** `uptime=4443.54` s, **`refresh_cnt=0`**, `latch=0x00`, `rcv_ovf=0`, `sdvga_ovf=0`, smooth FPS observed by user, `vfc` advanced `55323 -> 60253` |

Note on `sticky=0x01` and `side_cnt=2`: those fields were already `0x01` / `2` from the prior `plus8` / `plus24_norcv` runs and were not cleared before `plus24_rcvonly`; `refresh_cnt` was reset (`44 -> 0`) before `plus24_rcvonly` and remained `0` throughout Movie 4K 24p.

**Decisive isolation result:**
- Keeping `rcv_desc` stock while advancing `FE + size_rows` by `+24` (`plus24_norcv`) **still fails** (`refresh_cnt = 44`).
- Advancing `sensor + rcv_desc` by `+24` while keeping `FE + size_rows` stock (`plus24_rcvonly`) **passes at full smooth FPS** (`refresh_cnt = 0`).
- Therefore **`R1` (`rcv_desc` mismatch) is refuted**. The upward `Lost CAMOUT_V` trigger resides **100% inside the `FE` (`fe_rec_0a`/`fe_rec_19`) + `size_rows[16,17]+0x3c` (`rt_t14+0x50`) write group**.

## 4. Live `watch 60` Trace of `sgc1_HV` and `sgc1_win` in Movie 4K 24p (`plus24_rcvonly`)

Captured at 50 ms cadence while transitioning MSC -> Movie 4K 24p -> MSC (`PID 3090`, `/tmp_bt/diag.log`):

```
t=    0ms..28000ms  ref=0 l=0 rcv=0 sdvga=0 st=0x00 vfc=55323 sgc=(858,1750) win=(00,00,00) src=(0,0)
t=28250ms           ref=0 l=0 rcv=0 sdvga=0 st=0x00 vfc=55323 sgc=(862,1740) win=(01,1d,1b) src=(0,0)
t=29000ms           ref=0 l=0 rcv=0 sdvga=0 st=0x00 vfc=60105 sgc=(862,1740) win=(01,1d,1b) src=(0,0)
t=30000ms           ref=0 l=0 rcv=0 sdvga=0 st=0x00 vfc=60163 sgc=(862,1740) win=(01,1d,1b) src=(0,0)
t=31000ms           ref=0 l=0 rcv=0 sdvga=0 st=0x00 vfc=60223 sgc=(862,1740) win=(01,1d,1b) src=(0,0)
t=31550ms..         ref=0 l=0 rcv=0 sdvga=0 st=0x00 vfc=60253 sgc=(858,1750) win=(00,00,00) src=(0,0)
```

Observed behavior of `SGC1` block `+0x40..+0x42` (`PA 0x04339E38..0x04339E3A`):
- In MSC: `sgc=(858,1750)`, `win=(0x00, 0x00, 0x00)`.
- In Movie 4K 24p: `sgc=(862,1740)`, **`win=(0x01, 0x1d, 0x1b)`** (`+0x40 = 1`, `+0x41 = 29`, `+0x42 = 27`).
- These are small mode-dependent window bytes (`29`, `27`), not stock-valued vertical line counts in the `3376..3447` range.

## 5. FE Binary-Search Isolation (`plus24_fe [0..33)` down to `[0..1)` vs `[1..66)`)

Using `a6400_og_ram apply plus24_fe <lo> <hi>` (built from `tools/research/a6400_og_ram.c`, `625,604` B, SHA-256 `3f39e4269fe7bbb109c5aea4f21534b260fd39ef61df9c5ce678ea7007ca2e5b`, `readback_verified: true`), every step patched `reglist[0..3]` (`+24`, `Y=(330, 3420)`) plus `[lo..hi)` of the `66` FE offsets in both `fe_rec_0a` and `fe_rec_19`, leaving `size_rows`, `rt_t14`, `rcv_desc`, and timing at `STOCK` with `noreboot` enabled (`0x40`):

| Step | FE offsets patched (`+24`) | `APPLY` receipt | `uptime` (s) | Result in Movie 4K 24p |
| --- | --- | --- | --- | --- |
| `plus24_fe [0..33)` | First 33 FE offsets (`+0x002..+0x1A0`) | `written=68 unchanged=90` | `4847.18` | **FAIL:** `refresh_cnt=20`, `rcv_ovf=0`, `sdvga_ovf=0`, low FPS |
| `plus24_fe [0..16)` | First 16 FE offsets (`+0x002..+0x0AA`) | `written=34 unchanged=124` | *(battery pull)* | **FAIL:** severe low FPS / stutter prevented USB MSC re-enumeration (`ref` unavailable; rebooted to `uptime_s=103`) |
| `plus24_fe [0..8)` | First 8 FE offsets (`+0x002..+0x032`) | `written=18 unchanged=140` | `229.83` | **FAIL:** `refresh_cnt=53`, `rcv_ovf=0`, `sdvga_ovf=0`, low FPS |
| `plus24_fe [0..4)` | First 4 FE offsets (`+0x002..+0x016`) | `written=10 unchanged=148` | `293.04` | **FAIL:** `refresh_cnt=32`, `rcv_ovf=0`, `sdvga_ovf=0`, low FPS |
| `plus24_fe [0..2)` | `+0x002` (`3442->3466`), `+0x00A` (`3392->3416`) | `written=6 unchanged=152` | `346.85` | **FAIL:** `refresh_cnt=19`, `rcv_ovf=0`, `sdvga_ovf=0`, low FPS |
| `plus24_fe [0..1)` | **Only `+0x002` (`3442->3466`)** | `written=4 unchanged=154` | `405.43` | **FAIL:** `refresh_cnt=17`, `rcv_ovf=0`, `sdvga_ovf=0`, low FPS |
| `plus24_fe [1..66)` | **All 65 FE offsets except `+0x002`** (`+0x002=3442` stock) | `written=132 unchanged=26` | `504.77` | **FAIL:** `refresh_cnt=22`, `rcv_ovf=0`, `sdvga_ovf=0`, low FPS |

### Synthesis (OBSERVED + INFERRED)

- **OBSERVED:** Raising **only a single FE field** (`+0x002`: `3442 -> 3466`) breaks `CAMOUT_V` (`refresh_cnt=17`), **and** keeping `+0x002` stock while raising `[1..66)` **also** breaks `CAMOUT_V` (`refresh_cnt=22`). By contrast, `test1` (`Y_SIZE=4020`, FE stock), `plus24_rcvonly` (`sensor + rcv_desc +24`, FE stock), `timing4`, and `shrink` (`-160`) all pass.
- **INFERRED:** Because *independently* pushing either `fe_rec + 0x002` (`3442 -> 3466`) OR the remaining `[1..66)` fields (`+0x00A = 3392 -> 3416`, etc.) fails `CAMOUT_V` with zero `rcv_ovf` / `sdvga_ovf`, the failure is not caused by an internal mismatch *between* `fe_rec[0]` and `fe_rec[1..65]`. Instead, **the upstream stream arriving into FE is still delivering only the stock vertical line count (`3396` / `3442`), so any FE stage configured to wait for `> stock` lines stalls `CAMOUT_V`** (either `IMX284` active height did not actually increase beyond `3396` in 4K 24p, or an upstream CAMIN/MIPI/SLVS crop/receiver register still stops at `3396` lines).

## 6. Restoration and Repeatability Receipts

Final camera cleanup command and receipts after `plus24_fe [1..66)`:
- `/tmp_bt/a6400_og_ram putback`: `PUTBACK: ok restored=132 unchanged=26`
- `/tmp_bt/a6400_og_ram noreboot off`: `NOREBOOT off: flag 0x40 -> 0x00 refresh_cnt=22`
- `/tmp_bt/a6400_og_ram resetcnt`: `RESETCNT: 22 -> 0`
- `/tmp_bt/a6400_og_ram dump`: `COUNTS: total=158 stock=158 ... other=0`, `VERDICT: STOCK`
- `rm -f /tmp_bt/a6400_og_ram /tmp_bt/diag.log && ls -la /tmp_bt`: only stock BT/mod entries remain (`ble_local_keys`, `bsa_s_pid`, `bt-avk-fifo`, `bt-daemon-socket`, `mlv2`, `mods`).
- `cat /proc/uptime`: `504.92 1734.13`; subsequent `usb-state`: `uptime_s=507`, `modules.custom=none` (monotonic after the `[0..16)` battery pull).

## 7. Next Step

1. Verify visually / statically whether `test1` (`Y_START=30`, `Y_SIZE=4020`) actually changes sensor framing in Movie 4K 24p, and trace `fe_rec + 0x002` (`3442`) + `fe_rec + 0x00A` (`3392`) consumers alongside upstream CAMIN/SLVS receiver line registers (`0xF3120xxx` / `0xF30xxxxx`) and non-`reglist` `IMX284` control paths to confirm why FE stalls as soon as any single FE height field exceeds stock.
