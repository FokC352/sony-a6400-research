# E0729 — QEMU `cxd90045_i2c`: Model `i2c.ko` Pre-Write Busy-Poll and Unconditional Status Reads

```text
ID: E0729
Date: 2026-11-26
Claim: Disassembly of `/kmod/i2c.ko` establishes that `wait_reg_not_busy` (`+0x12b8`) polls bit 15 (`0x8000`) == 0 on `+0x10` (`I2C_T10`), `+0x14` (`I2C_T14`), and `+0x20` (`I2C_ABORT`) before writing them, and `+0x15c4` reads `+0x00` (`I2C_STATUS`) and tests bit 5 (`0x20`) even when `complete_transfers` is false. Modeling both rules unconditionally in `cxd90045_i2c.c` retires all 4 `cxd90045.i2c.unmodelled` read pairs on live `ILCE-6100` and `ILCE-6400` `--d-unimp` boots (`8 -> 4` distinct pairs).
Status: verified-offline (9/9 `tests.test_qemu_peripherals_and_models` PASS, `tools.research.ci_check` PASS, live `sony-a6100` and `sony-a6400` QEMU `--d-unimp` boots verified)
```

## Firmware Contract (`/kmod/i2c.ko`)

- `i2c.ko +0x12b8` (`wait_reg_not_busy`): polls `ldrh` on `+0x10` (`I2C_T10`), `+0x14` (`I2C_T14`), and `+0x20` (`I2C_ABORT`) with `tst r3, #0x8000` until bit 15 is clear before writing.
- `i2c.ko +0x15c4`: reads `+0x00` (`I2C_STATUS`) and tests bit 5 (`ands r2, r3, #0x20`) for bus/NACK error regardless of whether transfer completion (`complete_transfers`) is enabled.

## Verification Commands & Results

1. **QTest & peripheral regression suite**
   - Command: `.venv/bin/python -m unittest tests.test_qemu_peripherals_and_models -v`
   - Result: `Ran 9 tests in 2.369s OK` (includes `test_i2c_prewrite_busypoll_and_status_reads_without_complete_transfers`).
2. **Repository & guardrail checks**
   - Command: `.venv/bin/python -m tools.research.ci_check`
   - Result: `PASS` (`py_compile`, `test_discovery_guard`, `binary_and_size_guard`, `markdown_links`, `status_svg_sync`, `guardrail_tests`).
3. **Live QEMU `--d-unimp` boots**
   - `sony-a6100` and `sony-a6400` boots (`timeout 150`, `sequencer.step=26`, `vph_ch53=true`): `unmodelled.distinct_pairs` dropped from `8` to `4` (retiring `0x2401000`, `0x2401010`, `0x2401014`, `0x2401020`).

## Remaining Limitations

- Four unrelated unmodelled read pairs remain on default `sony-a6100` / `sony-a6400` (`f290 +0x2900030`, which is covered by `clkrst-model=on`, and `tpu +0x2404010/110/210`, whose idle status word is intentionally left unmodelled in `cxd90045_tpu.c`).
- No physical camera was accessed; all checks ran offline.
