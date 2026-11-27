# E0718 — ILCE-6400 BL protocol: capability gate, lens bitmap, and the three-bit `0x1D` mode

- **Date:** 2026-11-26
- **Target:** `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin` (16,674,456 B, SHA-256 `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`, ORIL base `0x635C6000`); `.../nflasha15_unpacked/lib/libObj.so` (20,860,436 B, SHA-256 `60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1`)
- **Method:** static only. Capstone 5.0.7 Thumb disassembly (`tools`-style helper `dis/hex/xrefs/str`), PC-relative literal resolution, 51-site BL census. No camera, USB, SD, `/dev/mem`, emulation or flash access. Companion lens-side record: E0719.
- **Labels:** [C] read directly from code/bytes, [I] inference, [L] public web lead (leads only, never contracts).

## Question

E0648 decoded the focus command IDs and posited a capability gate and a first-generation `0x1D` layout. Three things remained open and block any lens-side work:

1. what exactly a lens must declare, and where the declaration comes from;
2. whether a third-party lens is structurally excluded from the second-generation commands;
3. what the `0x1D` mode byte actually encodes.

## Result

- The **capability bitmap is not in the `0x09` STARTUP_INFO item**. The accessor is `0x6385CA68` (`0x6385CA80` is its `bl`), and item `0x09` is status-only: bytes `[8]` and `[9]` must be zero or the body logs `LIF: [BL]SYNC START UP INFO ERR` and retries.
- The bitmap lives at **`BL_LENS_DATA + 1`**, where `BL_LENS_DATA` is the pointer variable at **`0x642FE7B8`** (literal slot `0x645A9088` = module base `0x645A21E4` + `0x6EA4`; the same slot the gate null-tests, and the code's own `LIF: BL_LENS_DATA null %d` log confirms the name): 32 bytes, bit `(id-1)`, byte index `((id-1)>>3)&0x1F`. This part of E0648 is **confirmed**; the producer attribution is **corrected**. A later lane had read the variable as `0x644FED18`; that value came from an unaligned/offset slip in the `add rX,pc` arithmetic — the literal at `0x63855FF8` is `0x00D4C244` and the `add` at `0x63855F9C` uses `A+4`.
- The gate is a **per-query intersection** of the lens bit with a body mask, never a merged copy: `0x63855FD8` requires the lens bit, `0x63855FE8` returns the body-mask bit at `bank*0x20 + ((id-1)>>3)` with `bank = *(u8*)0x6448B366`.
- The body mask page 0 at `0x63F0439B` is a 32-byte bitmap: bytes 0–8 are all `0xFF`, byte 9 is `0xEF`, byte 10 is `0xFF`, byte 11 is `0x0F`, and bytes 12–31 are zero. Decoded, that means **every ID from 0x01 to 0x4C and 0x4E to 0x5C is allowed**; `0x4D` and `0x5D+` are not. There is **no lens-ID dimension** in the mask.
- The only identity-based block is a three-ID inhibit: `0x63856B24` sets the inhibit byte **`0x6448B4A0`** when the lens u16 at `+0x2b`/`+0x2c` equals `0x8036`, `0x8037` or `0x8038` and camera byte `*(0x640A8960)+0x21` bit 0/1/2 is set.
- The second-generation family is gated as a group, but through a **predicate, not a path**: `0x63D13492` calls the gate for `0x45, 0x49, 0x46, 0x44, 0x47, 0x48` and returns 0 as soon as one is denied; only when all six pass does it return a byte fetched by `0x63857DFC` (a four-instruction accessor). The "all allowed ⇒ new path" behaviour is visible at the callers, e.g. `0x63D0F702`: `bl 0x63D13492; cbz r0, skip` with the new-generation calls before the label.
- The `0x1D` mode is **three bits**, not two. E0648's "bit 2 flag" is mode bit 2. Every call site of the builder `0x63D0F158` maps to wire mode `0`, `3`, `4` or `6`, and the operand type tracks the mode without exception: absolute position (0/3), signed `-16/0/+16` counts (4), signed `(delta>>4)` with a ±1 floor (6).
- The distance-code law is confirmed by its inverse: `0x63D0D5C2` computes `exp2((code−0x180)×8)`, i.e. `D = 2^((code−384)/64)`; `0x63D0D592` maps 0 to `0x700` and clamps there.
- The cyclic containers `0x05`/`0x06` are a per-frame **snapshot journal**, not the AF path. The journal ring `0x642FB180` (32 × 0x68) is copied forward with `memcpy(new, prev, 0x68)` and has exactly one consumer (`0x63D0F044`) that serialises ten fields into a telemetry stream. The AF path reads the live sub-item table (`0x63858AD6`/`0x63858AF0`).
- **There is no timeout or age check.** An item the lens omits silently keeps its previous value.
- `libObj` publishes lens fields as `(0xC0xx key → enum value)` to observers and keeps **no persistent handler-map or lens-state table**; the temporary map is a stack object at `r7+0x650` (`r7 = sp+8`), which corrects E0649's "object member" reading. It does copy the 138-byte category-6/`0x2053` message to object `+0xcc` (`0xA3A1E6`), so a **last-message copy exists** — E0649's "no persistent copy" is narrowed accordingly.

## A refuted public claim

`E-mount-protocol-RE` @`1680204c` (`docs/lens_id.md`) states that for third-party lens IDs the body mask collapses to `0x01–0x40` plus `0x4B/0x4C`, so `0x41–0x4A` are "never sent to a third-party lens, whatever the lens declares". **Refuted for this image**: the mask table has no lens-ID dimension and allows `0x41–0x4A` for every lens; the only identity-based block is the three-ID inhibit above. The claim may describe another body generation; it is a lead, not a contract.

## Inputs and commands

All addresses are av-cam addresses; file offset = address − `0x635C6000`.

| Purpose | Command |
| --- | --- |
| gate leaf | `python -m tools.research.bl_disasm dis 0x63855F94 0xA0` |
| cached ID table | `python -m tools.research.bl_disasm dis 0x63855F60 0x28` |
| body mask page | `python -m tools.research.bl_disasm hex 0x63F0439B 0x40` |
| inhibit writer | `python -m tools.research.bl_disasm dis 0x63856B24 0x80` |
| group gate | `python -m tools.research.bl_disasm dis 0x63D13480 0x170` |
| `0x1D` builder | `python -m tools.research.bl_disasm dis 0x63D0F158 0x60` |
| mode-0 fallback | `python -m tools.research.bl_disasm dis 0x63834480 0x50` |
| Q14 normaliser | `python -m tools.research.bl_disasm dis 0x63D0F4E0 0x40` |
| distance law | `python -m tools.research.bl_disasm dis 0x63D0D592 0x40` |
| STARTUP_INFO | `python -m tools.research.bl_disasm dis 0x6385CA68 0x140` |
| journal copier | `python -m tools.research.bl_disasm dis 0x63835CE0 0x40` |
| journal consumer | `python -m tools.research.bl_disasm dis 0x63D0F044 0x70` |
| `0x43` deadline timer | `python -m tools.research.bl_disasm dis 0x63838E48 0x140` |
| `0x41` POS_CYCLE | `python -m tools.research.bl_disasm dis 0x6383869C 0xA0` |
| DividedInfo chunks | `python -m tools.research.bl_disasm dis 0x63859A60 0x100` |
| gate census | `python -m tools.research.bl_disasm xrefs 0x63D12860` (51 references) |
| libObj dispatch | `python -m tools.research.bl_disasm dis 0xA35258 0x60 --image libobj` |
| libObj key map | `python -m tools.research.bl_disasm dis 0xA3A8D0 0xF0 --image libobj` |

`--image avcam` is the default; `libobj` disassembles at ELF vaddr with no load bias.

Every `[C]` claim above was re-derived by an independent read-only verifier in a fresh context; three intermediate readings were disproved in that pass (a claimed "same value under two modes" path, a claimed `0x1D→0x1F` converter, and a claimed mode-1 init record) and are **not** carried here.

## Not established

- Which wire item fills `*(0x642FE7B8)`; the bitmap's carrier ID is most plausibly `0x01` [I], not read.
- The writer of the mask row selector `0x6448B366` (BSS, zero at boot; no reference found).
- The meaning of `0x8036`/`0x8037`/`0x8038` (lens model vs class).
- The physical unit of the `0x49` velocity word, and of the `0x41` SendCom/DividedPos counts.
- Who writes the frame-period word `0x642FC68C` and the VD counter byte `0x6448B4A7` (outside this image).
- Whether a lens that claims `0x41–0x4A` gets better AF on the body. **No physical test was done.**

## Next boundary

Decode the store into `0x642FE7B8` to name the bitmap carrier wire item, then test the three-bit mode against a lens that implements it (a passive bench harness or a scope on a native lens during AF-C). The lens-side counterpart is E0719.
