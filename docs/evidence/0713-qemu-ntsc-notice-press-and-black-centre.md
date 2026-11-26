# E0713 — The stock NTSC notice, a declared rear-centre press, and the black live-view centre

Date: 2026-11-26.

This record covers two diagnostic findings about the stock power-on NTSC notice on the emulator: the notice
is one unchanged image from 60 s to 180 s, and a declared rear-centre press dismisses it near 130 s but
does not reveal a live-view frame. These runs are diagnostic and unscored. The boots use camera-captured
inputs that are not in the repository. This record claims no physical camera result.

## Observation

- In one sampled boot per model setting, the head-0 VPH image at 60, 90, 100, 120, 150 and 180 s was the
  same image in all twelve samples and in both reference oracle runs: 4,368 non-black pixels with centre
  fraction 0.0314, and panel-enable `0xF3440144` held at `0x01000233` throughout.
- A red-team re-check found the twelve sample files and the two reference files byte-identical and
  identified the image as the opaque NTSC notice: a bordered frame with one centred line of glyph text,
  16 grey levels.
- That check did not send a press, so it shows what the notice looks like but not what a press would do.
- The blocker in this configuration is therefore the stock power-on NTSC notice, a modal that covers the
  live-view channel and waits for a user press; it is not the P0.2 parameter and not capture timing up to
  180 s.

## Stimulus

- The follow-up experiment used one declared probe: a single QMP `send-key` of qcode `ret` through the
  rear-panel centre path, with hold time 180, on the promoted diagnostic build with rear controls enabled.
- The probe was sent at 100 s in two boots, at 130 s in one boot, and not sent in two control boots. The
  rear ADC channel read the key as held (45 against 1023 released) in both timings, so the press reached
  the model.

## Result

- A press at 100 s changed nothing: the 4,368-pixel notice stayed at all six samples with centre fraction
  0.0314, and both boots ended at index 42, step `0x897F`, with 0 mismatched modules.
- Both control boots also ended at index 42, step `0x897F`, with 0 mismatched modules.
- A press at 130 s dismissed the notice: by 150 s the frame was 17,258 non-black pixels with centre
  fraction 0.0002, a black live-view centre.
- The final sequencer state in the 130 s boot was index 42, step `0x897F`, with 0 mismatched modules, so
  the press did not move the boot.
- The red-team re-check reproduced the late press in two of two boots, kept centre fraction 0.0002 at
  every sample to 295 s, and found the same final state.
- Read-only memory reads give the cause: the live-view channel was enabled but its start address was
  `0xfe16a400`, inside the model's always-zero window `0xFE100000` and above; the two bar buffers at
  `0x14e90000` and `0x14fbc000` were filled but no longer scanned, so the compositor painted the
  live-view layer black.
- One declared variant with a second identical press made the centre fraction 0.589, above the 0.5 gate,
  but the image was a modal on-screen warning about focus mode, not live view, and the channel still
  pointed at `0xfe16a400`.
- The red team's verdict was upheld with correction: a single press is not a frame-gate result, and a
  second press can satisfy the metric without any live-view pixel.

## Inputs

- Camera-captured boot inputs and the camera-captured storage images; neither is in the repository, and
  their contents are not described here.
- The declared probe is the only synthetic input: one QMP `send-key` of qcode `ret`, and in one variant a
  second identical press.
- Boot options of the runs: the promoted diagnostic environment plus rear controls, live-view source
  `bars`, and `-icount shift=0,sleep=on`.

## Build and commands

- Build: the promoted diagnostic QEMU build named in the run summary.
- The press was delivered over QMP; the VPH head-0 image was sampled by screendump at 90, 99, 110, 125,
  150 and 175 s, and the sequencer was read at the end.
- The red-team re-check sampled every 20 s from 135 to 295 s with pre-press samples at 90 and 125 s, and
  read the channel registers, the bar buffers and the start address by QMP `pmemsave`.

## Limits

- Emulator only; the camera-captured inputs are not in the repository, so the counts cannot be reproduced
  without them.
- Diagnostic and unscored, because the probe is a declared stimulus and not part of the scored ladder.
- The press result is for this build and this declared timing; it does not show what the physical camera
  does on a rear-centre press.
- The frame metric counts any non-black centre, so an on-screen dialogue can pass it without live view;
  that weakness belongs to the human-owned ladder.
