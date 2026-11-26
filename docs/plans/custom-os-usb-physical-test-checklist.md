# Custom OS USB physical-test checklist (not executed)

Prepared from the offline evidence in
[E0716](../evidence/0716-custom-os-usb-dwc3-phase1-diagnosis-and-composite-model.md) and the plan's
Phase 4. **Nothing in this file has been run on a camera.** It is a proposal for a future,
explicitly authorized RAM-only session with a deadman timer.

## Preconditions

- The offline gates are green: Phase A, Phase B, the composite guest test and the DWC3 qtest
  contract (116/116).
- The bundle is built from the current initrd and carries a deadman (`deadman=90` or longer).
- The camera is in Mass Storage, the stock debugger answers (`PING`), and `/tmp_bt` is clean
  before the run.
- No flash/SD write, no service mode, no firmware install, no driver replacement.
- The user has given an explicit GO for this specific run.

## Proposed read-only verification before the jump

1. `ping` the stock debugger and record `usb_state` (module list, `/sys/class/udc/dwc3.0`).
2. Record `/proc/uptime`, `/tmp_bt` listing and the stock `/setting/a6400_usb_debugd.log` tail.
3. Confirm the camera's own `dwc3.0` state is `configured` and the extcmd transport responds.

## Proposed RAM-only run

1. Stage the same payload as the offline bundle and run the `RAMBOOT.KO` dryrun.
2. Live `insmod` with the deadman; the loader branch starts `imlaunch`.
3. After the jump, the expected host-visible sequence is the one proven offline:
   `GIFT step=start rc=0`, enumeration with two interfaces and five endpoints, and a vendor
   `PING` -> `PONG` on the debug channel.
4. Record from the host side: the enumerated descriptors, the PONG payload and timing, and the
   first OUT completion (if a host tool is attached).
5. Teardown: `DISCONNECT`/stop, then the deadman; verify the camera returns to stock, the stock
   debugger answers again, `/proc/uptime` is monotonic and `/tmp_bt` is clean.

## What must not be concluded from a pass

- A vendor `PING`/`PONG` is not the stock `a6400_usb_debugd` transport and not a Sony class
  function.
- Enumeration is not composite UVC or PTP behaviour.
- The offline model is a contract model, not silicon; a physical mismatch must be recorded as a
  new boundary with PC/arguments.
