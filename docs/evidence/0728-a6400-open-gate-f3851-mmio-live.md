# E0728 - ILCE-6400 Open Gate `0xF3851xxx` / `0xF3951xxx` Live MMIO Read During `plus8`

- **Date:** 2026-11-26
- **Scope:** Read-only single-page `E0647`-authorized `devmem_policy.py` MMIO inspection of `0xF3851000` and `0xF3951000` (`0x3A8`, `0x3B8`, `0x418`, `0x41C`) at baseline (`STOCK` MSC) and live during `plus8` (`dy = +8`, Movie 4K 24p, `noreboot` set), followed by clean restoration to `STOCK`.
- **Predecessors:** `E0724`, `E0725`, `E0726`, `E0727`.

## 1. Policy, Width Verification, and Tooling Identity

- `tools/devmem_policy.py 0xF3851000 0xF3951000`: both pages classified `GO` inside `IOTABLE` (`0xF0000000..0x05000000`), explicitly authorized by the user for read-only inspection.
- Bus width check in `artifacts/hardware/av-cam-camera-original.bin`: `0x6375FFAC` dispatches to `0x63CCB142` (`0xF3851300`) and `0x63CCB304` (`0xF3851400`), both using 32-bit `str` (`str r3, [r0, #off]`). Therefore `read_mmio_u32` in `tools/research/a6400_og_ram.c` reads aligned `volatile uint32_t *` inside a single 4 KiB `mmap(PROT_READ)` with `sigsetjmp`/`SIGBUS` handling (`625,788` B, SHA-256 `4dbb203db204145ca0c87b107ef305a438f499a4bceddbecb43a0c6c824e6130`, `readback_verified: true`).

## 2. Live Observations (`STOCK` MSC vs `plus8` Movie 4K 24p)

Baseline in MSC (`VERDICT: STOCK` `158/158`):
- `0xF38513A8 = 0x00000000`, `0xF38513B8 = 0x00000000`, `0xF3851418 = 0x00000000`, `0xF385141C = 0x00000000`
- `0xF39513A8 = 0x00000000`, `0xF39513B8 = 0x00000000`, `0xF3951418 = 0x00000000`, `0xF395141C = 0x00000000`
- Zero `SIGBUS` or external aborts.

Live `mmio_e0702 60` trace during `plus8` (`APPLY plus8: ok written=144 unchanged=14`, `noreboot` `0x40`):

```
t=    0ms..18000ms  ref=0  vfc=445    85=[3A8:00000000 3B8:00000000 418:00000000 41C:00000000] 95=[3A8:00000000 3B8:00000000 418:00000000 41C:00000000]
t=18750ms           ref=1  vfc=15655  85=[3A8:00000000 3B8:00000000 418:00000000 41C:00000000] 95=[3A8:00000000 3B8:00000000 418:00000000 41C:00000000]
t=19250ms..22750ms  ref=4..26 vfc=15687..15855  85=[3A8:0000003f 3B8:0000003f 418:00000038 41C:0000003a] 95=[3A8:0000003f 3B8:0000003f 418:00000038 41C:0000003a]
t=23000ms..         ref=26 vfc=15863  85=[3A8:00000000 3B8:00000000 418:00000000 41C:00000000] 95=[3A8:00000000 3B8:00000000 418:00000000 41C:00000000]
```

**Observed register values in Movie 4K 24p:**
- `0xF38513A8 = 0x0000003F` (`63`)
- `0xF38513B8 = 0x0000003F` (`63`)
- `0xF3851418 = 0x00000038` (`56`)
- `0xF385141C = 0x0000003A` (`58`)
- `0xF39513A8..41C` mirror `0xF38513A8..41C` identically (`0x3F`, `0x3F`, `0x38`, `0x3A`).

## 3. Offline Closure of `[[parent+0x8C]]+0x3D8` (`0x6376E518`)

`0x63CCDB08` passes `[r4+0x8C]` (`out`, populated by `0x63CCF5C6` from `rec+0x108..`) into `0x63CD0C4A` -> `0x63CD0C66 bl 0x6376E518`. Therefore `out+0x3D8..+0x420` (`19` dwords) equals `fe_rec+0x4E0..+0x528` in `av-cam-camera-original.bin`:
- `fe_rec_0a+0x4E0..+0x528` (`idx 6`): `lo/hi` values `(411,351)`, `0`, `(395,341)`, `0`, `(395,335)`, `406`, `(405,342)`, `0`, `0`, `(0,389)`, `(6,6)`, `(1,14)`, `(6,6)`, `0`, `(336,396)`, `(2008,2376)`, `(2730,2730)`, `(25941,25941)`, `352`.
- None of the 19 dwords is a `3376..3453` stock vertical height limit; they are small sub-scaled/phase scalars (`2376/2008`, `2730/2730`, `352`).

## 4. Conclusion

- None of `0xF38513A8`, `0xF38513B8`, `0xF3851418`, `0xF385141C` (or their `0xF3951xxx` twins) holds a vertical line count (`3376..3453`) or `868`/`15100`/`1668`/`4752`; they hold small constants (`63`, `56`, `58`).
- `[[parent+0x8C]]+0x3D8` (`fe_rec+0x4E0..+0x528`) likewise holds no stock vertical height limit.
- **All in-image `av-cam.bin` carriers are now closed.** The upward `dy > 0` `Lost CAMOUT_V` limit is enforced on the external CAM front-end side across the ORIL boundary (`R5`).

## 5. Restoration and Cleanliness Receipts

- Restored via `putback`, `noreboot off`, `resetcnt`, `dump`, `rm -f /tmp_bt/a6400_og_ram /tmp_bt/mmio.log`.
- `ls -la /tmp_bt && cat /proc/uptime`: only stock entries remain (`ble_local_keys`, `bsa_s_pid`, `bt-avk-fifo`, `bt-daemon-socket`, `mlv2`, `mods`), `uptime = 7533.95 26654.64`.
- `usb-state`: `uptime_s=7525`, `modules.custom=none` (monotonic uptime, zero reboots, zero `SIGBUS`).
