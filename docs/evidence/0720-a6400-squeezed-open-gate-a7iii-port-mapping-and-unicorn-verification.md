# E0720 — ILCE-6400 Squeezed 3:2 Open Gate Mapping (`a7iii-open-gate-test` Port) and Unicorn Verification

- **Date:** 2026-11-26
- **Scope:** Offline porting and Unicorn verification of the A7 III squeezed 3:2 Open Gate technique (`ArcaneLantern/a7iii-open-gate-test`, commit `1644dac1c30a037f76064b410a96712edda7ed1b`, `og_m3_v2` / `m3og_v3`) against live ILCE-6400 `av-cam.bin`, plus read-only USB debugger state inspection. No physical RAM writes performed.

## 1. Input & Tool Identities

- **Firmware input:** `artifacts/hardware/av-cam-camera-original.bin` (`16,803,048` bytes, SHA-256 `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`, ORIL VA base `0x635C6000`, physical RAM base `0x035C6000` with `VA - PA = 0x60000000`).
- **Upstream reference:** `ArcaneLantern/a7iii-open-gate-test` commit `1644dac1c30a037f76064b410a96712edda7ed1b` (`windows/tools/camera_tests/K2_open_gate/helper/m3og_design.h`, `predictions.json`).
- **Verification tools:** `tools/research/a6400_open_gate.py`, `tests/test_a6400_open_gate.py`, WSL `arm-linux-gnueabi-objdump`, Unicorn (`C:\Python314\python.exe`, 3.14.6), and read-only `tools/camera_debugger.py`.

## 2. Mechanism & Comparison to Previous Approaches

Earlier ILCE-6400 Open Gate experiments (`E0578`, `E0620`, `mods/opengate_5400_test/opengate_5400_test.c`) attempted to resize the encoder/DMM/VPI canvas (`3240x2160`). Following A7 III `og_m3_v2` / `m3og_v3`, the squeezed Open Gate path keeps the stock `3840x2160` 4K H.264 stream/encoder configuration untouched and changes only three layers:

1. **IMX284 readout (`reglist`, `0x6454c484`):** Mode index `16` (`0x1F`, 24p) and `17` (`0x89`, 25p) both map through `0x63f109e1[mode]` to column `11`. Unlike A7 III (which also patches 3 OB/extra bytes in col 11), ILCE-6400 col `11` differs from col `10` (`4896x2760`) in only rows `10..13`: `Y_START` (`0x0025`/`0x001E`, stock `342 = 0x0156`) and `Y_SIZE` (`0x0027`/`0x001B`, stock `3396 = 0x0D44`), centered at `Y_START + Y_SIZE / 2 = 2040`.
2. **Front-End (FE) vertical squeeze (`fe_rec_0a = 0x63e14e94`, `fe_rec_19 = 0x63e15e54`, `1344` bytes each):** Selected by `0x63771d78` and `0x63771de4` for variant `10` (`0x0A`) and `25` (`0x19`). All 65 vertical `uint16` offsets identified on A7 III match 1-to-1 in both ILCE-6400 records (stock base height `3392` at `+0x00a`, `3442` at `+0x002`, scale factor `5818` at `+0x20e`): 60 offsets `+dy`, 2 offsets `+dy//2` (`0x1e4`, `0x1ec`), 2 offsets `+dy//4` (`0x250`, `0x254`), and `0x20e` `round(5818 * (3392 + dy) / 3392)`.
3. **Lockstep Timing (`timing_ntsc = 0x63e2580c`, `timing_pal = 0x63e259d4`, runtime `rt_t14 = 0x6420128c`, invariant `rt_t10 = 0x64200efc`):** Keeps frame period `h * v` nearly constant as active rows increase (`v_new = 1740 + dy // 2`, `h_new = round((h0 * v0) / v_new)`). Unlike A7 III (`h1 != h2`), ILCE-6400 stock 24p `(862, 1740)` and 25p `(827, 1740)` satisfy `h1 == h2` and `v1 == v2`; both pairs must be updated in ROM and in live `.bss` `rt_t14` (`+0x08/+0x0C` and `+0x68/+0x6C`), while `0x63ceff4e`'s derived byte `[rt_t10 + 0x24] = round(24.0 * h2 / h1)` remains `0x18` (`24`) by construction.

## 3. Graduated Patch Table (`dd8f8d0c...`)

| Step | `dy` | `Y_START` | `Y_SIZE` | FE `+0x002 / +0x00a / +0x20e` | Timing `24p (h, v)` [`h*v`] | Timing `25p (h, v)` [`h*v`] |
| --- | ---: | ---: | ---: | --- | --- | --- |
| `putback` (stock) | `0` | `342` (`0x0156`) | `3396` (`0x0D44`) | `3442 / 3392 / 5818` | `(862, 1740)` [`1,499,880`] | `(827, 1740)` [`1,438,980`] |
| `test1` (sensor only) | `+624` | `30` (`0x001E`) | `4020` (`0x0FB4`) | `3442 / 3392 / 5818` (stock) | `(862, 1740)` (stock) | `(827, 1740)` (stock) |
| `test4` (~4% squeeze) | `+156` | `264` (`0x0108`) | `3552` (`0x0DE0`) | `3598 / 3548 / 6086` | `(825, 1818)` [`1,499,850`] | `(792, 1818)` [`1,439,856`] |
| `test3` (~9% squeeze) | `+312` | `186` (`0x00BA`) | `3708` (`0x0E7C`) | `3754 / 3704 / 6353` | `(791, 1896)` [`1,499,736`] | `(759, 1896)` [`1,439,064`] |
| `test2` (3:2 Open Gate) | `+624` | `30` (`0x001E`) | `4020` (`0x0FB4`) | `4066 / 4016 / 6888` | `(731, 2052)` [`1,500,012`] | `(701, 2052)` [`1,438,452`] |

Runtime `rt_t14` `.bss` targets (populated at boot by `0x63cf0018`): mode `16` at `0x64201a14` (`+0x08`) and `0x64201a74` (`+0x68`), mode `17` at `0x64201a8c` (`+0x08`) and `0x64201aec` (`+0x68`) — VA to PA `- 0x60000000`.

## 4. Verification Results

- **Static disassembly:** Verified `0x6379d618` (`reglist` applicator), `0x63771d78`/`0x63771de4` (FE selectors), `0x6379d500` (table populator), `0x63ceff4e` (`rt_t10` populator writing `[+0x24] = roundf(24.0 * h2 / h1)` and `[+0x12]` from `h1`), `0x63cf0018` (`rt_t14` populator copying `timing_row` pairs), and `0x6379f00c` / `0x6379c54c` (`C_ImagerCtrl_IMX284` movie mode switch).
- **Offline + Unicorn (`python -m tools.research.a6400_open_gate --verify`):**
  - Passed for all 5 steps (`putback`, `test1`, `test4`, `test3`, `test2`, `142` ROM patch entries each).
  - Verified Unicorn execution of `0x6379d618` (`col 11` writes to `0x645a1fe8`, `0x645a1ed0`, `0x645a1dac`, `0x645a1f24`), `0x63771d78`/`0x63771de4` (`fe_rec_0a`/`fe_rec_19`), `0x63cf0018` (`rt_t14` pairs), and byte-exact in-memory `putback` restoration.
- **Unit tests (`python -m unittest tests/test_a6400_open_gate.py tests/test_sensor_tables.py -q`):** All tests pass on system Python 3.14 (`tests/test_a6400_open_gate.py`: `5` tests passed).
- **Read-only live camera check (`tools/camera_debugger.py`):** Camera attached via `usb_extcmd` (`pid=703`, `euid=0`, `uptime_s=3350`, `im.elf` `PID=149`). `maps 149` confirmed `0x635C6000..0x645CC4E8` is not user-space mapped in `im.elf` (LIRO physical DRAM `0x035C6000..` is excluded from standing `/dev/mem` reads per `tools/devmem_policy.py` and `E0314`/`E0647`).

## 5. Open Assumptions & Next Boundary

- `[rt_t10 + 0x12]` (`uint16` computed from `h1` in `0x63ceff4e`) and `rt_t10` initialization are populated at boot; on a warm running camera, patching `timing_rom` and `rt_t14` directly does not re-run `0x63ceff4e`. Whether `[rt_t10 + 0x12]` or higher IMX284 line rates (`+18%` at `dy = +624`) trigger `S-Ele Refresh` on physical `IMX284` hardware is unknown until tested on-camera.
- **Next boundary:** Explicitly user-authorized physical SMOD test run (`test4` -> `test3` -> `test2`, with `putback` and pre-write DRAM identity checks on physical `0x035C6000`) with user GO.
