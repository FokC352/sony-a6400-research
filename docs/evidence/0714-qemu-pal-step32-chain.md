# E0714 — The PAL step-32 chain: from the TV-system setting to the module-2 report

Date: 2026-11-26.

This record traces the chain that follows the opt-in PAL TV-system setting on the emulator. PAL removes the
NTSC banner and makes the frame gate true, but the boot stays at sequencer index 32 with the module-2
request at `0x032E` and the report at `0x0326`. Each link is marked PROVEN or INFERRED, and the refuted and
still-unknown parts are listed. The first PAL comparison was scored; all later runs are diagnostic and unscored. The boots use camera-captured
inputs that are not in the repository. This record claims no physical camera result.

## Observation

- The TV-system setting is a declared opt-in stimulus that rewrites one byte in a per-run copy of the
  backup storage; the scored comparison rerun was quiet and admissible.
- With PAL the frame gate passed (centre 1.0) but the boot regressed: index 42, step `0x897F`, 0
  mismatches and 53 distinct unmodelled pairs became index 32, step `0x8A3C`, 1 mismatch in bit 2
  (request `0x032E`, report `0x0326`) and 66 distinct unmodelled pairs.
- A red-team re-check upheld the regression with correction: the image was the reference bars chart under
  the guest's own on-screen display, with no PAL banner, and the PAL runs ended at index 32 in two of two
  while the NTSC control ended at index 42.

## Chain

- **PROVEN.** Under PAL the CamPig unit 1 runs at the guest-programmed 25.0 Hz timing (619 lines by 2325
  hclks, 39.977 ms per frame). At 2736 ms the guest writes the unit-1 control and re-arm registers to stop
  the path, and after that it makes no further unit-1 or unit-2 access; the last of five IRQ-36 edges is
  at 2697 ms. NTSC, by contrast, makes 900 arm writes to 17.5 s and 449 IRQ-36 edges.
- **PROVEN.** The re-check upheld that timing: PAL had 18 arms, 6 latches and 5 IRQ rises ending at
  2736-2737 ms, while NTSC had 900 arms, 449 latches and 449 IRQ rises to 17.5 s with no unit-1
  control-zero write.
- **PROVEN.** The stop is deliberate. At 2736 ms the guest calls the stop slots of the CamPig interruption
  object; they clear bits 26 and 27 of the unit-1 control register (final 0) and re-arm bits 2 and 3 of the
  re-arm register, and nothing restarts the path. The IRQ-36 acks at 2537, 2577, 2617, 2657 and 2697 ms
  are followed by an unacknowledged latch at 2737 ms.
- **PROVEN.** The re-check confirmed by static analysis and by the vtable that the stop slots perform that
  teardown, that the logged return addresses sit inside those functions, and that no unit-1 write follows
  2736 ms under PAL.
- **PROVEN (insufficiency).** Suppressing the command-0 stop did not clear the stall. In the suppressed
  runs there were no teardowns and 827 or 840 unit-1 IRQ edges over about 35 s, yet the boot still ended
  at index 32, step `0x8A3C`, with request `0x032E` and report `0x0326`.
- **CORRECTED.** The red team held the insufficiency result but narrowed its scope: clearing the flag skips
  all of case 0, release chain included, so only "skipping it is not sufficient" is shown; the counter is
  zeroed every pass unless the state is `0x32E`, so a zero counter is not independent evidence.
- **PROVEN.** PAL runs the same module-2 report writer and branch as NTSC; only the input value differs.
  All non-zero report writes are the setter store at `0x63D49B00`, reached from the only matching call
  site, and both systems take the same side of the branch and read the value through the pointer at
  `0x6436FD18`, written at `0x63DCFFDE`.
- **PROVEN.** In the logged windows the PAL report values were `0x300`:46, `0x320`:2, `0x323`:13,
  `0x326`:6, while NTSC had `0x300`:48, `0x302`:81, `0x320`:1, `0x323`:13 and `0x326`:3, plus `0x32E`
  three times at 2861, 2894 and 2928 ms.
- **CORRECTED.** The re-check corrected the NTSC counts (the earlier list omitted `0x302` eighty-one
  times), found that "PAL stops at `0x326`" depends on timing, and limited "PAL never writes `0x32E`" to
  the logged windows.
- **PROVEN (static, then confirmed at run time).** The report value `0x326` or `0x32E` comes from the fixed RAM
  pointer at `0x6436FD18`. Acceptance needs a flag byte written at `0x63BD5806` (observed address `0x6440E3DC`). The
  rule sets it only when the state word is `0x32E` and the two bytes at `0x6440D80E` and `0x6440D80F` are both
  non-zero, or when a force byte equals 2.
- **CORRECTED.** An earlier static reading said a frame counter above 6 also sets the flag, and tied the rule to the
  counter at `0x6440B0BA`. The run-time check showed both are wrong: that branch feeds a different field, the rule's
  own counter is at `0x6440E656`, and `0x6440B0BA` belongs to a separate per-pass object, whose counter increments
  only when [sp+`0xCE`] is non-zero and resets unless the state is `0x32E`.
- **PROVEN.** The board model has no TV-system branch, and several of its periods are fixed near NTSC: the
  SYSV vertical period 33367, the divide-by-60, the CamPig frame period and the 1 MHz event counter.
- **PROVEN (qualified).** The same index-32 ending with request `0x032E` and report `0x0326` occurred
  without PAL in four earlier cases, all under perturbed or probed conditions; clean NTSC runs never showed
  it, and one earlier claim was wrong as stated.
- **PROVEN (measurement), CORRECTED (cause).** On PAL passes whose state was `0x32E`, the source byte
  `0x6440D80E` read 0, the flag M[`0xBC8`] at `0x6440E3DC` stayed 0, and the report was `0x326`; NTSC read
  both source bytes as 1 and set the flag.
- **CORRECTED.** The source byte is downstream. The arm block at `0x63BD5D24` runs only when [sp+`0xCF`] is
  non-zero; under PAL it ran once with status `0x326` and never on the `0x32E` passes, because the producer
  at `0x63DD9346` sent no status event.
- **PROVEN.** The event-code publisher stops because the CamPig unit-1 teardown stops its IRQ. The producer
  arms status only when a record field changes; the publisher wrote it one or three times in PAL (frozen at
  `0x3d00`) and 33 times in NTSC, so on the PAL `0x32E` passes the status-arm and copy flags were zero, and
  G+`0x53` was 0 and not the blocker.
- **CORRECTED and PROVEN.** The re-check placed the publisher stop downstream of the known unit-1
  teardown: the publisher runs on the unit-1 IRQ, its last dispatch matches the last ack at 2697 or
  2730 ms, and the next frame runs the stop slots. The chain closes as report to flag to status arm to
  event code to unit-1 IRQ to teardown at about 2737-2770 ms.
- **REFUTED.** Model timing is not the discriminator. The SYSV register `0x3C` values and the evsel
  `0xE8`, `0xE4` and `0xEC` values were the same in PAL and NTSC to 2936 ms; NTSC's extra `0xBDD8` at
  3003 ms is after the PAL teardown, and there were no writes to SYSV `0x28` or `0x98`.
- **CORRECTED.** The re-check corrected the "identical counts" wording (`0x6978` appeared 47 times in PAL
  and 48 in NTSC), noted that the Sgc1 `0x220` and `0x224` writes differ and were already refuted as the
  trigger, and that several other registers were not logged and the diagnostic files do not record
  index 32.
- **PROVEN, with a statistical limit.** Loaded NTSC boots do not stall at index 32 but pass through it.
  Eight NTSC boots with two busy processes all ended at index 42, step `0x897F`, with 0 mismatches; of two
  quiet boots one was clean and one had the known bit-0 off-state, and no boot had a terminal index-32
  ending.
- **CORRECTED, with a limit.** The re-check found index 32 with request `0x032E` and report `0x0326` as a
  transient waypoint in clean NTSC boots, at 45-48 s in two of the runs and in a smoke run. The load was
  CPU only, not the earlier post-suite condition, and 0 of 8 excludes only per-run rates above about
  31 percent, so a rare load-tied race is not excluded.
## Refuted and corrected

- REFUTED: a writer stores `0x032E` under NTSC and never runs, or branches away, under PAL; the writer and
  branch are shared and the input differs.
- REFUTED: the raster or the Ydet floor explains the PAL stop; the raster follows the PAL geometry and the
  Ydet window is the same.
- REFUTED: a cumulative versus level re-arm register explains the stall; it changes only the latched mask,
  `0xC` versus `0x4`.
- CORRECTED: suppressing the stop tests only insufficiency, not necessity, because it skips the whole
  case-0 chain.
- CORRECTED: the frame counter above 6 does not set the flag.
- CORRECTED: "PAL never writes `0x32E`" holds only inside the logged windows.
- CORRECTED: the loaded NTSC test's two index-32 samples are inside the counted boots, not extra
  validation.

## Still unknown

- What makes the unit-1 frame at about 2737-2770 ms arrive without an acknowledgement, and what the
  teardown changed before the ack; the publisher is only the downstream witness.
- Whether PAL is necessary: the non-PAL counterexamples are all perturbed, so "PAL is required" is not
  disproved and "PAL causes it" is not proved.
- Whether the CamPig stop is necessary (only insufficiency was tested), and whether the missing report bit
  alone holds the sequencer.
- Whether the index-32 ending is a rare load-tied race; 0 of 8 excludes only rates above about 31 percent
  per run.
- The PAL value written to SYSV `0x3C` has no repository trace.

## Inputs

- Camera-captured boot inputs and the camera-captured storage image; neither is in the repository, and
  their contents are not described here.
- The declared TV-system setting rewrites one byte of the per-run backup storage; the read-only logging
  plugins are the only other instrumentation.
- The compared PAL and NTSC runs used the same build and the same camera inputs, one QEMU at a time.

## Build and commands

- Build: diagnostic trace builds (added logging only), read-only TCG plugins and the per-run backup setting; no
  model change was committed.
- The plugins log guest state and writes and do not read registers or write guest memory.
- The static gate claims were re-derived from the firmware image by static analysis and from the rule
  listing, not from a new boot.
- The loaded NTSC control used eight boots with two busy processes and two quiet boots, each sampled every
  3 s after a first sample at 45 s, with a 45 s stall hold.

## Limits

- Emulator only; the camera-captured inputs are not in the repository, so the counts cannot be reproduced
  without them.
- The diagnostic runs are unscored; the chain uses a declared TV-system stimulus and read-only plugins, not
  the scored ladder.
- An earlier finding showed the terminal report can shift under plugin overhead, so only per-pass patterns are compared, not the final index.
- The chain explains the PAL stall but does not prove that PAL is necessary, that the CamPig stop is
  necessary, or that the missing report bit alone holds the sequencer.
- No physical camera result is claimed.
