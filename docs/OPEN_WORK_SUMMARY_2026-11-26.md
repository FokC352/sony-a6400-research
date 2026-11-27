# Open Work Summary — 2026-11-26

The user reported that 5% of the usage cap remains. This report records the current QEMU audit status and the work still needed, in dependency order.

## Completed or verified

- The working branch is `main`. QEMU source, firmware, and regression-test changes are present in the local worktree but are **uncommitted**.
- The sub-micro model already parses TLVs and computes frame CRCs. HDMI hotplug reporting and synthesized idle A/B/C frames are implemented; the no-fixture HDMI hotplug regression passes.
- Chained DWC3 Bulk-OUT TRBs (`TRB_CTRL_CHN`) and corresponding firmware host behavior are implemented. The standalone chained write/readback test passed in an earlier run.
- SDHC/UHS status encoding was corrected: SDHC SCR security version, erase timeout/offset, and V30 status. The updated QEMU patch applies cleanly to the pinned QEMU v11.1.3 base.
- Verification in this session: `tests.test_qemu_peripherals_and_models` passed 14/14; `tests.test_qemu_usb_connections` passed all 17 runnable tests (5 optional native USB cases skipped because the ARM cross compiler is unavailable); `git diff --check` and `bash -n qemu-a6400/run.sh` passed.
- Earlier evidence E0715 already verifies substantial QEMU hardware contracts: natural av-cam step-42 progression without force probes, HDMI HPD, codec/capture primitives, UHS-I U3 media, overlay persistence/reload, and power-off/reset modeling. Those results do not prove complete interactive camera workflows.

## Remaining work, in dependency order

1. **Authentic ModelCamera readiness and stock USB entry.** Step-42 progression is not proof that the original `ModelCamera` readiness gate has cleared. E0619 records an original false readiness result and leaves the writer's controlling inputs unresolved. The only available extracted a6400 `libObj.so` has SHA-256 `60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1`, which does not match E0619's captured library (`e88b7e0fc61b8c98ac06c1979e6ebdd3fa8a2a49384b3fd5ffc8d06da4174ef1`). Do not apply E0619 offsets/contracts to the mismatched binary without re-deriving them. Resolve the original producer/readiness condition and then verify native `ActionOpenUSB`/`StartGadget`, reason-0x11 release, configuration events, and teardown.
2. **Power-off TLV and orderly backup save.** E0527's captured slot-B TLV `0x03` has bit 6 clear, so it is not evidence for a power-off acknowledgement. Do not invent a TLV response. E0715 verifies persistent overlay reload and the P10.5 power-off path, but the full guest orderly-shutdown-to-updated-`Backup.bin` persistence chain still needs direct verification.
3. **PMCA / camera_debugger adapter for the QEMU DWC3 token socket.** Build this on the existing EP0/Bulk TRB model and validate it against the original stock gadget after item 1. Do not inject guest readiness or USB events to bypass that dependency.
4. **Interactive playback, still capture, and movie recording.** JPEG/cRAW2, DMA, sensor, and movie HLE contracts exist (E0715); complete the stock WBI UI flows end to end: playback display, shutter-to-saved-file and reopen, and XAVC S/AVCHD recording. Keep Power Save/ModelRepair timing controlled and retain evidence receipts.
5. **BCM4339 802.11 data plane.** The model covers SDIO and BCDC control but not 802.11 data frames bridged to a QEMU netdev. Implement and verify this for stock Wi-Fi applications.
6. **MSC REQUEST SENSE timeout after write failure.** The combined native Mass Storage scenario still times out at REQUEST SENSE; earlier reproduction also occurred on baseline firmware. Isolate this independently of chained TRB behavior. The five native USB tests were skipped in this environment due to the missing ARM cross compiler.

## Next recommended actions

- Reproduce or acquire the exact E0619 firmware identity and inputs, then trace the original ModelCamera readiness writer and its controlling state. If those inputs are unavailable, record the missing identity-matched inputs as the blocker rather than substituting a different binary.
- In parallel, continue only evidence-backed sub-micro protocol analysis; retain the current no-response behavior for unverified TLVs.
- Once the root readiness gate is resolved, run stock USB entry and the PMCA adapter together, then proceed to UI storage/capture and Wi-Fi data-plane qualification.
