# E0696 — ILCE-6400 Squeezed 3:2 Open Gate Live RAM Preflight, Isolation Matrix, and Cleanup

- **Date:** 2026-11-26
- **Scope:** Physical ILCE-6400 (`CXD90045`, `dd8f8d0cc74ad4b7...`) RAM verification and graduated isolation campaign across `test1`, `timing4`, `test4`, `shrink`, and `plus24`, ending with full `putback` and `/tmp_bt` cleanup.

## 1. Tools & Contract Evolution

- **Table generator:** [tools/research/a6400_open_gate.py](../../tools/research/a6400_open_gate.py) -> [tools/research/a6400_og_ram_table.h](../../tools/research/a6400_og_ram_table.h) (`158` entries = `4` `reglist` col-11 bytes + `132` `fe_rec_0a`/`fe_rec_19` `uint16` fields incl. `+0x0fa` + `8` `timing_rom` pairs + `2` `size_rows[16,17]+0x3c` `V_ACTIVE` words + `6` `rcv_desc` `uint16` fields at `0x63e19740`/`0x63e19780` + `6` `.bss` `rt_t14` runtime words/pairs).
- **Camera binary:** [tools/research/a6400_og_ram.c](../../tools/research/a6400_og_ram.c) compiled via WSL `arm-linux-gnueabi-gcc -static -Os -mcpu=cortex-a5` and staged temporarily at `/tmp_bt/a6400_og_ram`.
- **Safety:** Single-page (`4096` B) `SIGBUS`-guarded `/dev/mem` mappings, `LIRO_HDR` + 100% pre-write validation, `dsb`, and full readback verification. Built-in `S-Ele` debug control: `PA 0x0434FD7E` bit `0x40` (`noreboot`), `PA 0x04367AF0` (`refresh_cnt`).

## 2. Live Hardware Isolation Matrix

| Step | Applied Layers | Observed Movie 4K (`24p`/`25p`) Behavior | Diagnostic Counter / State |
| --- | --- | --- | --- |
| Preflight `dump` + `putback` roundtrip | All `146 -> 158` entries verified `STOCK`, `apply -> putback -> apply` | `VERDICT: STOCK` and byte-exact restoration confirmed | `rt_t10 +0x12 = 1593` (`24p`), `1528` (`25p`); `+0x24 = 24` (`0x18`) |
| Initial `test4` (`dy=+156`, 65 FE fields) | Sensor + 65 FE + timing | **FAIL:** magenta tint, low FPS, reboot after ~5–10 frames | Magenta root-caused to `Y_START=264 % 4 == 0` Bayer phase flip; reboot in `klog-ring-dump.bin` (`PC 0x63907870`, `liro-kliro_101`, `S-Ele Refresh ng force exception`). Fixed via `dy % 8 == 0` (`test4` -> `dy=160`, `Y_START=262`). |
| `test1` (`dy=+624` sensor only) | `reglist` (`Y_START=30 % 4 == 2`, `Y_SIZE=4020`) only; FE & timing `STOCK` | **PASS:** normal colors, smooth FPS, no reboot (looks like stock because FE still crops `3376` rows) | Proves `IMX284` `4020`-row readout and Bayer alignment work cleanly (`uptime_s=293 -> 471`). |
| `timing4` (`dy=+160` timing only) | `timing_rom` + `rt_t14` `24p=(824,1820)`, `25p=(791,1820)` only; sensor & FE `STOCK` | **PASS:** smooth FPS, stable, no reboot | Proves `rt_t14` timing delta is accepted cleanly without touching `rt_t10`/`rt_t18`. |
| Updated `test4` (66 FE `+0x0fa`, `V_ACTIVE=3536`, `6` `rcv_desc`) | Full 158-entry `test4` (`dy=+160`, normal colors) | **FAIL:** normal colors restored, but low FPS + reboot (`S-Ele Refresh`); with `noreboot on` (`PA 0x0434FD7E \|= 0x40`), no reboot and `uptime_s` kept running | `refresh_cnt` rose `0 -> 33` (continuous `S-Ele Refresh`). Note `alog` (`0x03524FC0`) sits inside `wbimem` and only read `EVENTS`. |
| **`shrink` (`dy=-160`, timing `STOCK`)** | `Y_START=422`, `Y_SIZE=3236`, 66 FE (`3232`), `V_ACTIVE=3216`, `rcv_vtot=3287` | **PASS:** smooth full FPS, normal image, zero errors | **`refresh_cnt = 0`** (`0 -> 0`). Proves all 158 vertical height fields scale cleanly together for `dy <= 0`. |
| **`plus24` (`dy=+24`, timing `STOCK`)** | `Y_START=330`, `Y_SIZE=3420`, 66 FE (`3416`), `V_ACTIVE=3400`, `rcv_vtot=3471 < 3480` | **FAIL:** low FPS (no reboot due to `noreboot on`) | **`refresh_cnt = 27`**. Pinpoints a strict upper ceiling right above stock height (`3392`/`3442`/`3447`), even while `rcv_vtot=3471` fits under stock `VMAX*2 = 3480`. |

## 3. Conclusions, Cleanup & Next Boundary

- **Proved:**
  1. Vertical Bayer alignment requires `Y_START % 4 == 2` (`dy % 8 == 0`).
  2. All `158` patched vertical parameters (`reglist`, `66`-field `fe_rec_0a`/`fe_rec_19` incl. `+0x0fa`, `size_rows + 0x3c`/`rt_t14 + 0x50` `V_ACTIVE`, `0x63e19740`/`0x63e19780` `rcv_desc`) form a working closed vertical contract downward (`shrink dy = -160` ran at full FPS with `refresh_cnt = 0`).
  3. Increasing height even by `+24` lines (`plus24`, still under stock `VMAX = 1740`) immediately triggers `S-Ele Refresh` (`refresh_cnt = 27`), isolating the blocker to a fixed stock-height allocation (`DmmConfig.bin` / DMM pool / FE/SGC line buffer) rather than `VMAX` or timing.
- **Cleanup receipt:** Executed `putback` (`158/158 VERDICT: STOCK`), `noreboot off` (`flag 0x40 -> 0x00`), `resetcnt` (`-> 0`), `rm -f /tmp_bt/a6400_og_ram`, and verified `/tmp_bt` clean + camera responsive (`usb-state`).
- **Next boundary:** Offline analysis of `DmmConfig.bin` pools (`0x63ddf414`, `E0578`) and FE/SGC buffer stride/cap tables in `av-cam.bin` above `3376`/`3392`/`3442`.

## 4. Follow-Up Live `watch` Sampler (`E0697` Addresses Verified)

Added read-only `diag` and 50 ms `watch` samplers (`tools/research/a6400_og_ram.c`) covering `refresh_cnt`, `latch` (`0x044B02B9`), `rcv_ovf` (`0x044B02CA`), `sdvga_ovf` (`0x044B02C9`), `state_byte` (`0x0434D9AB`), `camout_vfc` (`0x043679DC`), and `sgc1_HV` (`0x04339E30`/`0x04339E34`) during active 4K 24p Movie transitions:

- **`plus24` (`dy=+24`, `V_ACTIVE=3400`, timing `STOCK`):** In MSC idle `sgc=(858,1750)`, `vfc=663`. At `t=22250ms` (4K 24p entry), `sgc` switched to `(862,1740)` and `vfc` jumped to `7065`. From `t=22300ms..24900ms`, `rcv=0`, `sdvga=0`, `st=0x00`, while `l=1` (`Lost CAMOUT_V`) fired every `vfc + 8` ticks (`7065 -> 7217`), advancing `refresh_cnt` `0 -> 20`.
- **`test4` (`dy=+160`, `V_ACTIVE=3536`, timing `(824,1820)`/`(791,1820)`):** At `t=19800ms` (4K 24p entry), **`sgc` switched to `(824,1820)`** — refuting the `E0697` hypothesis that `sgc1_HV` remained stuck at stock `(862,1740)`. Despite `(824,1820)` matching `rt_t14`, `rcv=0`, `sdvga=0`, `st=0x00`, and `l=1` (`Lost CAMOUT_V`) still fired every `vfc + 8` ticks (`11431 -> 11615`), advancing `refresh_cnt` `0 -> 24`+.
- **Implication:** With both `sgc1_HV` tracking `(824,1820)` and zero `RCV`/`SDVGA` overflows, `Lost CAMOUT_V` proves the Front-End never emits a `CAMOUT_V` end-of-frame pulse when configured height exceeds stock `3392` lines — isolating the physical ceiling either to an unpatched >3392 FE/CAMIN register or an unpatched IMX284 vertical readout clamp (`3396` active lines on wire despite `0x000A..0x000D`).
- **Second cleanup receipt:** Executed `putback`, `noreboot off`, `resetcnt`, removed `/tmp_bt/a6400_og_ram` and `/tmp_bt/diag.log`, verified `VERDICT: STOCK` (`158/158`) and camera responsive (`usb-state`).
