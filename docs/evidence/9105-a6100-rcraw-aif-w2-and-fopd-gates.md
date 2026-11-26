# E9105 — ILCE-6100 `RCRAW` / `AIF0 W2` (`0xF3002480`) and `S_SetFOPD` Gate Chain

- **Date:** 2026-11-26
- **Scope:** Offline static disassembly of captured `ILCE-6100` `av-cam.bin` (`16,803,048` B, SHA-256 `58b2118db858f3eb1594ae6cef3f628b7fa3cc6db9173b094565535d48e6ff9e`, `ORIL v4`, linked at `0x635C6000`). No camera, USB, SD, or emulator execution.

## 1. Inputs & Tools

- `av-cam.bin` (`16,803,048` B, SHA-256 `58b2118db858f3eb1594ae6cef3f628b7fa3cc6db9173b094565535d48e6ff9e`, `ORIL v4` at `0x635C6000`).
- Python 3.15 + Capstone `5.0.9` Thumb-2 disassembly and PC-relative/BL cross-reference scans.

## 2. Findings

### 2.1 `RCRAW_EN` (`0x643EE0F4`) / `RDRAW_EN` (`0x643EE4B0`) only drive `FOPD` and `AIF W0`/`W1`

1. `0x63B4C3F0` reads `0x643EE0F4` (`RCRAW_EN`, `ILCE-6400` `0x643D1810` equivalent) and `0x63B4C3FC` reads `0x643EE4B0` (`RDRAW_EN`, `ILCE-6400` `0x643D1BCC` equivalent).
2. `0x63B478EC` (called at `0x63912C24`) and `0x63B47E18` (called at `0x63912C32`) combine `(RCRAW_EN, RDRAW_EN)` via `0x63DC478C` and pass them to `0x63777380` (`ILCE-6400` `0x6376873C` equivalent).
3. `0x63777380` calls `0x63776B0C`, `0x63777308` (which calls `RCRAW CALC` at `0x637763B8` and writes `0xF30317C8`), and `0x637876F8` / `0x6378777C` (FOPD `RCRAW` hardware setup).
4. In the subsequent `PBL AIF` descriptor builder (`0x63777064`, called at `0x63B47C4C` and `0x63B48174`), `RCRAW_EN` (`0x63DC4D94`) and `RDRAW_EN` (`0x63DC4DA0`) only populate the `W0` (`+0x10`) and `W1` (`+0x20`) slots, **not** `W2` (`+0x40`).
5. The other two `bl 0x637763B8` (`RCRAW CALC`) callers (`0x63B4B69C` and `0x63B4B892`) belong to `[IDT]: [idt_opdtop_set_intn_capdat]` (still capture) and `[IDT]: [idt_opdtop_set_intn_strob_dat]` (strobe), while `0x63B493F0` (`[IDT]: RCRAW can't work 8pix/clk %d`) sets `[0x643EDE10 + 0x64/+0xE0]` for the `<= 4pix/clk` FOPD path.

### 2.2 `AIF0 W2` (`0xF3002480`) `RCRAW` path is gated by `S_SetFOPD` / `Astra` (`fc_prc_request_controller.cpp`)

1. In `0x63B478EC` and `0x63B47E18`, the `AIF W2` `RCRAW` slot (`[sp+0x2C + 0x40]`, populated by `0x63DC4DA6`, clearing `SDEV` `[+0x30] = 0`) is only executed when `[0x643EDF60] != 0` (returned by `0x63B4AA8C()`).
2. When `0x63DC4DA6` runs, `0x63777064` checks `[r4, #0x30]` vs `[r4, #0x40]` (logging `[NSX_OPD]: PBL RCRAW AND SDEV AIF CONFLICT ERROR. W2.` if both are non-zero) and either:
   - programs AIF channel `0x14` (`W2`) via `0x63CE4576` (`SET_FUNC`) / `0x63CE4550` (`SET_LOOP`) when `[r0, #0x24] == 0`, or
   - writes `[r0, #0x20]` (`[Astra]debug_out_phy_addr`) to `0xF30317CC` and `(w * h * 2)` to `0xF30317D0` when `[r0, #0x24] != 0`.
3. `0x63B478EC` / `0x63B47E18` also skip `0x63777064` entirely unless `([r4, #8] | [r4, #9]) == 1` (`0x63B47BAA` / `0x63B480EA`) and `[0x644B8604] == 0` (`0x63B4E0BC`).
4. The sole runtime writer to `0x643EDF60` is `0x63B4AF1C` (via `0x63B47674` / `0x63B476F4` from `0x63B483C0` `S:S_SetFOPD`, whose sole caller is `0x6393C194` in `0x6393C0C4`).
5. `0x6393C0C4` copies `[r4, #0x654..#0x67B]` and `[r4, #0x640]` (`0x63D4DD0E` checks `[r4, #0x640] == 1`) into `S_SetFOPD`. Those fields are populated by `0x6393BDA8`, which requires:
   - `0x63930A90([r4, #0xE94])->[+8]` having non-zero `u16` dimensions at `+0x6A8` and `+0x6AA`;
   - `0x63DBAA82` (`0x63B2E80C`, `FC/Misc/fc_prc_request_controller.cpp::SyncGetPrcFlow`) returning `1` (which calls `0x63B2E254` `CheckSyncGetPrcFlow` and `0x63B2DF20` in `FC/Misc/fc_prc_controller.cpp`; both call `0x63DBEFBA` `CalcSdevOutSize`, which switches on `0..4` and hardcodes output dimensions `640×360`, `640×426`, or `640×480` — proving `Astra`/`SDEV` on `AIF0 W2` is also `<= 640×480` FOPD-sized, not full-sensor video);
   - `0x638F89DC()` (`[0x6455F1B0]` DFS clock state in `{2, 3}`); otherwise `0x6393BFEE` clears `[r4, #0x640]` back to `0`.

### 2.3 `SET_RAW_EXTERNAL_OUTPUT` (`0x63BEFFA0`)

- Referenced from the command table at `0x645973A0` (`0x63BEFF9D` Thumb), `0x63BEFFA0` stores payload fields into `0x6442452C + 0x878/+0x87C/+0x880`, invokes `0x63BEF634`, and its companion handler `0x63BEF694` dispatches `0x2044` / `0x2042` messages via `0x63D51E90(4, ...)`; command dispatcher `0x63BF06A6` calls `0x63BF0034`, which gates on `[GOT+0x4A38]+0xE0` and `*[GOT+0x454] == 0x00BF0274`.

## 3. Conclusion & Next Boundary

- Both `RCRAW_EN` (`0x643EE0F4` / `ILCE-6400` `0x643D1810` -> `0x63777380` `160..640` FOPD) **and** `S_SetFOPD` / `SyncGetPrcFlow` (`0x63B2E80C` -> `0x63DBEFBA` `CalcSdevOutSize` `640×360/426/480` -> `AIF0 W2` `0xF3002480`) are hard-capped at `<= 640×480` FOPD/Astra frames, not full-sensor >8-bit video RAW.
- Remaining boundary for **F01**: qualifying `SET_RAW_EXTERNAL_OUTPUT` (`0x63BEFFA0`, `0x2044`/`0x2042` IPC, `0x00BF0274` gate) and `IMX284` / `IDT` `0xF3000200..0xF3000422` capture in Unicorn/QEMU and on real hardware (cannot be completed offline without `unicorn`, WSL/QEMU, and camera access).
