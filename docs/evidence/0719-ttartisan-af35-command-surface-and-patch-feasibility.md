# E0719 — TTArtisan AF 35 mm F1.8 II E: command surface, controller, offline harness, and patch feasibility

- **Date:** 2026-11-26
- **Target:** `ttartisan_raw.bin` (STM32F1 application, Thumb, load base `0x08004800`), 22,004 B, SHA-256 `12af0702e588da7fae0391d3a707e611f3775ee1c3fdaf88cdcdd98425a413`; container `TTARTISAN_AF_35_F1.8_II_E_20251218  V.05.fir`, 23,236 B, SHA-256 `ae8d9460d37d10e6528219c08e4c1f13aeea9cd83c16d3cf9a853f3cb8b077a7`
- **Companion:** E0718 (body side). The lens image lives outside this repository; only addresses, sizes and short excerpts are recorded here.
- **Method:** static only. Capstone 5.0.7 via `python -m tools.research.bl_disasm` (`--file <path> --base 0x08004800`), plus a Unicorn 2.1.4 harness. No `.fir` was built, nothing was flashed, no lens or camera was touched, no bus was captured.
- **Labels:** [C] read from code/bytes, [I] inference.

## Question

The body side (E0718) shows that the ILCE-6400 will drive any lens with the second-generation focus commands if the lens declares them, and that the legacy `0x1D` record carries a three-bit mode. What does the TTArtisan actually implement, what would a compatibility patch have to change, and can that be validated without hardware?

## Result

- **Command surface:** eight frame IDs — `0x01, 0x08, 0x0A, 0x1B, 0x34, 0x3B, 0x8E, 0x8F` — plus `0x03`/`0x04` special-cased for the legacy item scan (`0x0800713C..0x080071E6`). Items recognised inside `0x03`/`0x04`: `0x1C`, `0x1D`, `0x1F`, `0x22`, `0x2E`, `0x2F`, `0x3C`, `0x3D`.
- **No second generation in the decoded stream:** eight `cmp` instructions carry immediates in `0x41..0x4B` (`0x08007988..0x080079C8`), but all of them test payload bytes of the `0x8E/0x8F` `"DEBG"` string — **no command-ID dispatch** on those values exists. No handler or reply for `0x41/0x43/0x44/0x45/0x49/0x4A` is visible. Absence inside the undecoded `0x08007F80..0x080083A3` stretch and the compressed tail from `0x080090D0` is **not** established.
- **`0x1D` mode is discarded:** `0x0800504C` reads only `[1..2]` into state `0x20001B40`, ignores `[3]` and `[4]`, sets `+0x3E = 2` and pending `+0x42 = 1`.
- **No capability bitmap and no lens ID** are visible in the decoded stream. The only identity is the ASCII string `TTARTISAN 35mm F1.8 ` at `0x080097F7..0x0800980A` inside a compressed tail with zero code references. The plausible bitmap carrier (the reply buffer at `ctx+0xC`) is a shape argument only: the two templates are `0x30` apart, seven bytes more than a 41-byte bitmap frame needs.
- **Unknown IDs fall through silently** (`0x080079D6`): the lens sets `state[2] = 2` and returns, so the body never learns a command was ignored.
- **The receiver does not validate the checksum or the `0x55` terminator.** The lens accepts any frame whose `0xF0` session bit and `buf[1]` length are plausible.
- **Controller:** `0x1D` executes immediately (no queue); `step = |target − position| >> 4`, clamped to `[1, 25]`; **no deadband**; both settle tests require exact `pos == target`; backlash take-up is `±127` counts in the `0x1F` path; the profile update is decimated to one in sixteen timer ticks.
- **Completion is implicit:** the status telegram built by `0x080071F4` carries the position pair and derived fields; no `0x1C`/`0x1D` acknowledgement byte is ever written to a transmit buffer.
- **Debug backdoor:** IDs `0x8E`/`0x8F` with payload `[7..11] == "DEBUG"` set internal flags `ctx[0x6D]`/`ctx[0x77]`.
- **The container encoding is not decoded.** The `.fir` body is `0x260` header + 22,004 bytes + `0x270` trailer, but no encode path is available. The body is **not content-keyed**: the first three 32-bit container words are identical (`0xB5BFA634`) while the corresponding payload words differ and are the initial stack pointer and the reset vector (`0x20002280`, `0x080049D1`). That rules out a content-keyed map only — a **position-keyed** keystream or a block cipher is not excluded, and the scheme stays unidentified. Whole-body reversal and 4/8/16/32/64/256/1024-byte block reversals were also tried and fail. **No modified `.fir` reproduces its same-named `.bin` at the patch bytes**, while the `.bin` patch is semantically meaningful (at image `0x08005282` the stock `bne` becomes a `nop`). Every existing `.fir`-side patch value is therefore untrustworthy, and no `.fir` can be rebuilt today.
- **The payload is full:** 243 `0xFF` bytes but no run longer than 2, the longest zero run is 16 B (reserved vector slots), and `nop` padding never exceeds two instructions. The largest contiguous idle space is 16 B and is unusable — **no code cave**. RAM is not the constraint: 11,648 B free above the `0x20002280` stack top, and existing BL frames are 24 B.
- **Existing mods:** `ttartisan_patched.bin` = deadband `#8`, speed floor `3`, `0x1F` nudge `127→16`, one arrival-gate `bne`→`nop`; `ttartisan_v07_stable.bin` = deadband `#2`, speed window `[3,18]`, nudge `10`, the other arrival-gate nop; `ttartisan_v07_patched.bin` adds three unrelated unsafe edits (unconditional peripheral disable `0x08006284`, removal of the `state[+0x68]==1` motion gate `0x08008826`, hard-coded 10-count ramp `0x08008850/6E`) plus a cosmetic name change.

## Start-up declaration and cyclic data (L16/L17 cross-checks)

These two conclusions were produced after the main static passes, against the already-recorded lane results.

- **Declaration cannot be volunteered.** The reply dispatcher `0x08007B9C` switches on `[0x20001B84]`, the ID of the *last received command* (`0x080078BC`), so a `0x01` frame exists only as an echo of a body request.
- **There is nowhere to build it.** The `0x01` reply would be the fixed 0x30-byte buffer at `0x20001BA4`; its only reader is the ID-1 arm `0x08007C78`, and it has **no decoded writer** (the sole other reference, `0x08008394`, is inside the undecoded `0x08007F80..0x080083A3` stretch).
- **The gate bytes are not a declaration.** The bytes at the body's `L+9`/`L+10` offsets come from the live focus value `state+0x20` (`0x080072A8`, `0x080072B0`), and `state+0x20` is itself written from a **body** frame (`0x080070AC`, `buf[9..10]`).
- **Declaring would disarm the lens.** On the body, `0x63834392` takes the legacy `0x1D` mode-0 path only when the `0x43` gate returns 0, so setting the bits removes that fallback while the lens still answers `0x41/0x43/0x49` with silence (`0x080079D6`). Bits cannot be declared without handlers.
- **The cyclic data is insufficient for the legacy loop as implemented.** The lens sends one fixed-layout frame, not an item set; the builder never reads the controller position `+0x2C` or target `+0x40`; item `0x12` degenerates to zeros (`0x63858B0E`); there is no explicit at-rest or direction; and the reported pair contains the body's own echo. With no timeout on either side, a silent lens and a stalled lens are indistinguishable.
- **Sigma gives this lane nothing.** `sigma_raw.bin` is a sparse Sigma lens-*data* image (65.1 % `0xFF`, per-4 KiB entropy mean 2.34, model string `18-50mm F2.8 DC DN | Contemporary 021`), not readable as ARM or Thumb (Thumb coverage 34.4 % against a 95.4 % random baseline; `bx lr` 0 vs 114 in this lens; no window with two distinct BL command IDs; no code-like shared n-grams). The `.fir` container word `0xB5BFA634` appears in no Sigma file. **A data image cannot answer a protocol question**; the comparison is not comparable. The entropy, census, model-string offset and magic census were re-computed independently and reproduced; the lane's island grouping did not.

## Offline harness (validated)

A Unicorn 2.1.4 harness loads the image at `0x08004800`, maps RAM `0x20000000`, the STM32 device-ID page `0x1FFFF000` and a zeroed peripheral window `0x40000000`, sets `SP = 0x20002280`, and calls the real handler `0x080078B0` with a synthetic frame.

- IDs `0x1B`, `0x1D`, `0x41`, `0x43`, `0x49` return to the LR sentinel with **no unmapped access** and leave different RAM effects; `0x1D` writes state `0x20001B84`/`0x20001B86`/`0x20001B89`.
- ID `0x34` faults at PC 0 because that arm calls a RAM function pointer the real start-up has not yet installed — a harness limitation, not a firmware fault.
- Because the receiver does not check the checksum, synthetic frames are accepted; a faithful motion test still needs the real start-up and timer path.

## Ranked patch proposal (summary)

1. **Deadband** at `0x08005208` (stock has none; both settle tests need exact equality). 6 bytes, no relocation. Risk: the body's tolerance is unmeasured.
2. **Speed clamp** at `0x08005214/18/20` to fit the body's one-frame budget. 2–4 bytes.
3. **Backlash** at `0x08005278/7C/90/94` (±127). 4 bytes.
4. **Honour the `0x1D` mode byte** (~80–140 bytes): does not fit without displacing the `DEBUG` backdoor and the duplicated `0x3C` gate; mode 3 can reuse the existing `0x2E` distance converter.
5. **Second generation** — not feasible until the container is decoded, a longer payload is shown acceptable, and the six-ID group is implemented together.

**A capability bitmap alone is explicitly not recommended**: the body's mask allows `0x41–0x4A` for any lens (E0718), so declaring them without handlers would flip the body onto a path the lens cannot answer.

## Commands

| Purpose | Command |
| --- | --- |
| frame dispatch | `python -m tools.research.bl_disasm dis 0x080078B0 0x120 --file <ttartisan_raw.bin> --base 0x08004800` |
| item scan | `python -m tools.research.bl_disasm dis 0x08007048 0xA0 --file <path> --base 0x08004800` |
| `0x1D` handler | `python -m tools.research.bl_disasm dis 0x0800504C 0x48 --file <path> --base 0x08004800` |
| speed law | `python -m tools.research.bl_disasm dis 0x08005208 0xA0 --file <path> --base 0x08004800` |
| reply builder | `python -m tools.research.bl_disasm dis 0x080071F4 0x180 --file <path> --base 0x08004800` |
| control frame | `python -m tools.research.bl_disasm dis 0x08006E58 0x60 --file <path> --base 0x08004800` |
| vector table | `python -m tools.research.bl_disasm hex 0x08004800 0xC0 --file <path> --base 0x08004800` |
| identity string | `python -m tools.research.bl_disasm str TTAR --file <path> --base 0x08004800` |
| container header | `python -m tools.research.bl_disasm hex 0x0 0x40 --file <V05.fir> --base 0` |

## Not established

- The container encode path; every `.fir` rebuild claim is blocked on it.
- The lens's capability bitmap and lens-ID value (possibly inside `0x08007F80..0x080083A3` or the compressed tail from `0x080090D0`).
- The body's arrival tolerance, the tick period of the periodic task, and the position source (`+0x2C` has no store in the recovered instructions).
- Whether the body latches one of the inhibited IDs for this lens.
- **No physical test was done** for any proposed change.

## Next boundary

Decode the container, then re-decode the `0x08007F80..0x080083A3` stretch and the compressed tail to settle the bitmap/identity question. After that, a one-variable bench test (deadband only) is the cheapest physical confirmation, and the harness is ready to pre-validate it.
