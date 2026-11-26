# E0708 — Boot score for ZERO-only windows on four write-only pages at `0xF2931000`, `0xF3203000`, `0xF3212000` and `0xF3215000`

Date: 2026-11-25 (measurement date). Written 2026-11-26.

This record covers one emulator change: four pages whose baseline traffic is write-only are given
per-offset ZERO-only windows. The scores below were produced by this project's boot scorer
(oracle version 3) on the scored build, configured with `--disable-sdl`. This record does not
claim any physical camera result.

## Observation

Before the change, the baseline boot listed 82 distinct unmodelled address pairs. Ten of them are
single 4-byte writes in four pages:

- `0xF2931000`: `+0x100`, `+0x104`, `+0x12C`, `+0x130`
- `0xF3203000`: `+0x384`, `+0x904`
- `0xF3212000`: `+0x05C`, `+0x068`, `+0x06C`
- `0xF3215000`: `+0xB00`

All ten are writes; none is a read in the baseline boot. Every access fell through to the
device-wide catch-all, which drops writes and returns 0 on a read. The claim is a **static
classification from the observed traffic**: these offsets are write-only in this boot, not a
modelled device. Two other pages that the same experiment reached, `0xF2932000` and `0xF35D0000`,
are read in the baseline and are deliberately left unmodelled.

## Change

The change is in `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c`:

- **Four windows:** one `0x1000` window at each of `0xF2931000`, `0xF3203000`, `0xF3212000` and
  `0xF3215000`, at priority `-600` (above the `-1000` catch-all), each with its own rule table.
- **Ten rows:** one `CXD90045_F292_ZERO` row per relative offset listed above (4 + 2 + 3 + 1).
  No table holds a read-back (`RF`) row.
- **Write path unchanged:** a write to a ruled offset is accepted, is stored in the window's
  register file, and stops logging; the stored word is never exposed because a ZERO read does not
  return it.
- **Read path:** a ZERO row returns 0 and does **not** log. Only an offset that matches no row in
  the same page still logs as unmodelled. The value matches the catch-all (0); the log line does
  not.
- **Tests:** a new static check (no QEMU boot) asserts the rows per window, that no table holds an
  `RF` row, that each base is created once with size `0x1000` and its own table, that no read-page
  table or create exists, that no window overlaps another window, that the ten pairs match the
  baseline the change was scored on, and that they are absent from the current baseline after
  promotion. The two baseline checks skip when the scoring baseline is not present.

## Result

Each column is one score file as recorded. No values are averaged. Round 3's two runs disagreed,
so it is reported as recorded and was not admissible. Rounds 1–3 were flagged contended; their
speed is not judged.

| Measure | Before (baseline) | Round 1 | Round 2 | Round 3 | Round 4 (quiet) | Promotion |
| --- | --- | --- | --- | --- | --- | --- |
| Identity check | ok | ok | ok | ok | ok | ok |
| Final sequencer step | 42 of 43, `0x897F` | same | same | 42 of 43, `0x897F` in one run; **28 of 43, `0x800E`** in the other | same | same |
| Mismatch count | 0 | 0 | 0 | 0 in one run; **1** in the other | 0 | 0 |
| Distinct unmodelled pairs | 82 | **72** | **72** | **72** | **72** | **72** |
| Unmodelled reads | 44 | 44 | 44 | 44 | 44 | 44 |
| Accesses to unmodelled space | 5,352,659 | 5,577,484 | 4,671,650 | 5,294,458 (on-state run) | 5,285,485 | 5,387,324 |
| `t_reach` | 51.61 / 51.68 s | 51.92 / 55.81 s | 104.94 / 69.18 s | 57.56 s (on-state run only) | 52.03 / 46.38 s | 51.5 / 46.37 s |
| Contended host | no | yes | yes | yes | no | no |

- **Every scoring round:**
  - **Round 1** (two runs, host loaded, maximum load1 3.03 and 7.46, flagged contended): both runs
    ended at index 42, `0x897F`, 0 mismatches, 72 distinct pairs. Speed not judged.
  - **Round 2** (two runs, load1 16.85 and 12.23, contended): both runs at index 42, `0x897F`,
    0 mismatches, 72 distinct. Speed not judged.
  - **Round 3** (two runs, load1 6.34 and 3.94, contended): one run at index 42, `0x897F`,
    0 mismatches; the other ended at **index 28**, step `0x800E`, with one mismatch, bit 2,
    request `0x0326`, report `0x0323`. The pair disagreed, so the score was inadmissible and
    nothing was promoted from it.
  - **Round 4** (two quiet runs, load1 1.24 and 1.57, not contended): both runs at index 42,
    `0x897F`, 0 mismatches, 72 distinct; the runs agree. Accepted under the two-run rule.
  - **Promotion re-measure** (two quiet runs, load1 1.53 and 1.89): both runs at index 42,
    `0x897F`, 0 mismatches, 72 distinct.
  - **Total:** 10 runs, 9 at `42 / 0x897F / 0` mismatches, one at index 28.
- **Why the quiet rescore counts:** the baseline itself is **not** deterministic. The sources
  record **1 off-state run in 26 quiet baseline runs** (about 4%): 15 of 16 at `42 / 0x897F / 0`
  and 1 at `42 / 0x897F` with one mismatch (bit 0, request `0x0102`, report `0x0118`), against an
  earlier 10/10. The single off run here happened while the host was loaded (load1 6.34 and 3.94)
  and a separate worker was running; the quiet rescore and the promotion re-measure both agreed.
  The result is therefore accepted as coverage, not as proof that the change is deterministic.
- **Scorer verdict:** the comparison was accepted as coverage. The change removed ten distinct
  pairs and added none (82 to 72), and the final machine state is the same in every on-state run.
  Not moved past the sequencer step.
- **Red team (static):** verdict upheld with a correction. It checked that the ten rows match
  the ten baseline write pairs exactly, that a ZERO read returns the same value as the catch-all,
  that the catch-all sits at priority `-1000` below the `-600` windows, that no other mapping at
  priority `-600` or above overlaps the four pages, and that the diff touches only the board file
  and the new test. The correction: "same final state" was only a static argument at that point
  and was left to the boot scorer. **No driver code was analysed**, so it is not shown that the
  firmware has no load path for these offsets. The source of the 0 value is the earlier baseline
  catch-all capture, not a datasheet and not a bus capture.
- **Timing:** the scorer samples every 5 s, so the `t_reach` differences outside the loaded rounds
  are about one sampling step and are not a speed claim.
- **Related runs on window builds:** a later diagnostic variant of these same windows, with the
  catch-all's per-access logging restored, ran 2 off-state in 10 quiet runs. Across all builds
  that added or varied such windows around that date the sources count 6 off-state runs in 34
  (about 18%), against 1 in 26 for the baseline. At these sample sizes the difference is not
  significant (p about 0.1), so a small effect of the change on this timing race is neither shown
  nor excluded.

## Silent reads

The ZERO windows are **silent on a read**. In the read path the unmodelled log is emitted only
when no rule matches; a `CXD90045_F292_ZERO` rule returns 0 with no log. This hides any read of
these ten offsets: if the firmware ever reads one, it gets 0 with no unmodelled record, so a value
it needed would not appear in the log at all. It also hides the stored write value, since a ZERO
read never returns it. The change makes the log quieter; it does not make the model more correct.

## Inputs

- Camera-captured boot inputs: the boot manifest and the submicro camera page. Neither is in the
  repository.
- Camera-captured storage images holding the boot initrd and the driver modules. Not in the
  repository; their contents are not described here.
- The camera-captured `IDT_CAM` preload. Not in the repository. Its contents are not described
  here.
- Boot options of the scored runs: `EVSEL_LATE_US=500`, `CAMPIG_ENABLE_READBACK=1`,
  `SGC1_EVENT_A=1`, `GATE_FORCE_PROBE=1`, `LIVEVIEW_SOURCE=bars`, `ENTRY_FORCE_PROBE=1`,
  `WORK_DMA=1`, speed profile `default`, `-icount shift=0,sleep=on`. `GATE_FORCE_PROBE=1` and
  `ENTRY_FORCE_PROBE=1` are declared probes that the scorer allows. Every option is identical
  before and after, so the difference is this change alone.

## Build and commands

- QEMU v11.1.3 with this repository's `qemu-a6400` overlay, built in a fresh build directory,
  configured with `--disable-sdl`.
- Test: the new static ZERO-window check (no QEMU boot); it passes.
- Each boot was scored by the boot scorer, oracle version 3, with two runs per score.
- Identity: the instruction digest, the boot manifest digest and the submicro camera page digest
  are the same in every round and in the promotion. The QEMU binary digest is `64d3d37b…` in all
  five post-change score files, as expected for one scored build (`1df761e5…` before).

## Limits

- Emulator only. The camera-captured inputs are not in the repository, so these counts cannot be
  reproduced without them. The record claims no physical camera result.
- The change adds coverage only. It does not move the firmware past its sequencer step, which
  stays at 42 of 43 with 0 mismatches in the on-state runs.
- The write-only claim is a static classification of the observed traffic, not a bus capture.
  A read of any of the ten offsets still returns 0, but silently, with no unmodelled log.
- Two pages that are read in the baseline, `0xF2932000` and `0xF35D0000`, are deliberately left
  unmodelled; this record makes no claim about them.
- The baseline is not deterministic: its own rate is about 1 off-state run in 26, so one off-state
  run is not by itself evidence that this change broke anything.
