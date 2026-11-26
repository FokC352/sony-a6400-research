# E0732 — QEMU `setup-qemu.sh`: Remove Stale `qemu-overlay/hw/sd/sd.c`

```text
ID: E0732
Date: 2026-11-26
Claim: Removing the stale `qemu-a6400/qemu-overlay/hw/sd/sd.c` prevents `setup-qemu.sh` (`cp -R qemu-overlay/.`) from overwriting the `hw/sd/sd.c` UHS-I U3 hunks (`speed-class`, `uhs-speed-grade`, `CMD11`, `SCR[33]`, `ACMD13`) applied by `qemu-v11.1.3-sony-a6400.patch`.
Status: verified-offline (`test_uhs_u3_sd_voltage_switch_tuning_and_ssr_speed_class` PASS, `tools.research.ci_check` PASS)
```

## Verification Commands & Results

1. **UHS-I U3 qtest**
   - `.venv/bin/python -m unittest tests.test_qemu_clkrst_pmic_uhs_hdmi.QemuPrerequisitesHardwareTests.test_uhs_u3_sd_voltage_switch_tuning_and_ssr_speed_class -v` -> `Ran 1 test in 56.640s OK`
2. **Repository & guardrail checks**
   - `.venv/bin/python -m tools.research.ci_check` -> `PASS`

## Remaining Limitations

- Offline QEMU qtest verification only; no physical hardware or SD card was accessed.
