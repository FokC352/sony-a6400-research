# E0726 - ILCE-6400 Open Gate `plus24_r4` Live Test: Refutation of `R4` (FE Band Clamp Pair `rec+0x194`)

- **Date:** 2026-11-26
- **Scope:** RAM-only live isolation test on the physical ILCE-6400 (`plus24` plus `fe_rec_0a+0x194` and `fe_rec_19+0x194` set `0 -> 24` with `noreboot` set) to test candidate `R4` (`E0725` section 10). No MMIO device-map pages touched. Recovered via battery pull to fresh `STOCK`.
- **Predecessors:** `E0723`, `E0724`, `E0725`.

## 1. Inputs and Tooling Identity

| Item | Value |
| --- | --- |
| Reference firmware | `artifacts/hardware/av-cam-camera-original.bin` (`16,803,048` B, SHA-256 `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`, ORIL VA `0x635C6000`) |
| Target ARM helper | `artifacts/tests/a6400_og_ram` (`625,732` B, SHA-256 `775b5984ac984ed6074c6cdc57850852226b5a31eea2addb704f11cd03ef0f7e`, `readback_verified: true`) |
| Added `R4` targets | `fe_rec_0a+0x194` (`PA 0x03E15028`, VA `0x63E15028`, stock `u32 = 0x00000000`) and `fe_rec_19+0x194` (`PA 0x03E15FE8`, VA `0x63E15FE8`, stock `u32 = 0x00000000`), patched `0 -> 24` (`0x0018`) alongside `plus24` |

## 2. Execution and Live Observation

Pre-write baseline in MSC (`usb-state` `uptime_s = 1231`, `modules.custom = none`):
- `/tmp_bt/a6400_og_ram dump`: `LIRO_HDR: MATCH @ 0x035c6000`, `COUNTS: total=158 stock=158 ... other=0`, `VERDICT: STOCK`.
- `/tmp_bt/a6400_og_ram noreboot on`: `NOREBOOT on: flag 0x00 -> 0x40 refresh_cnt=0`.
- `/tmp_bt/a6400_og_ram resetcnt`: `RESETCNT: 0 -> 0`.
- `/tmp_bt/a6400_og_ram apply plus24_r4`:
  - `APPLY plus24: ok written=144 unchanged=14`
  - `APPLY plus24_r4: ok fe_194=(24,24)`
- `/tmp_bt/a6400_og_ram diag`:
  - `DIAG: refresh_cnt=0 side_cnt=0 latch=0x00 sticky=0x00 rcv_ovf=0x00 sdvga_ovf=0x00`
  - `DIAG: debug_flag=0x40 state_byte=0x00 camout_vfc=173 camout_frc=6671991`
  - `DIAG: fe_194=(24,24)`, `rt_t14[16/17]` `V_ACTIVE=3400`, `rcv_desc=[3420, 3416, 3420, 3400, 3416, 3471]`.

**Live Movie 4K 24p result (OBSERVED by user):**
- Switching from MSC to Movie 4K 24p produced **no image at all** (black screen, worse than `plus24`'s low-FPS output) and required a physical battery pull to recover. Because the camera lost live view and had to be power-cycled, no post-run `diag`/`refresh_cnt` could be read from that boot.

## 3. Conclusion - `R4` Refuted Live

- Under `R4` (`E0725` section 10), raising `rec+0x194` alongside `rec+0x198` (`+0x428` and `+0x42C` shifted symmetrically by `+24`) was predicted to eliminate asymmetric band clamping distortion and rescue `plus24`.
- In reality, setting `fe_rec_0a+0x194 = 24` and `fe_rec_19+0x194 = 24` with `plus24` completely suppressed output image generation rather than restoring full-FPS live view.
- **`R4` is therefore refuted.** Only two candidates from `E0725` survive:
  - **`R5`:** a stock-valued vertical reference / producer outside `av-cam.bin` (sub-LSI / DSP publish side of `p = *(0x643691FC+4)` and `0x639303D0` callback, or the actual sensor active-height control since `Y_SIZE` in `0x000A..0x000D` is not the frame length).
  - **`R6`:** the SGC-adjacent receiver window `0xF3122000`/`0xF3123000` (`+0x144`, `+0x148`, `+0x160`), whose struct base comes from unclosed `0x63799D04`.

## 4. Recovery and Cleanliness Verification

After battery pull back to MSC (`STOCK` RAM):
- `C:\Python314\python.exe tools/camera_debugger.py usb-state`: `uptime_s=81`, `transport=usb_extcmd`, `modules.custom=none`.
- `C:\Python314\python.exe tools/camera_debugger.py exec "ls -la /tmp_bt && cat /proc/uptime"`: `/tmp_bt` contains only stock entries (`ble_local_keys`, `bsa_s_pid`, `bt-avk-fifo`, `bt-daemon-socket`, `mlv2`, `mods`), `uptime = 81.98 270.07`.
