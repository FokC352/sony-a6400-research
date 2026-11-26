# E0709 — Boot score for a read-back window on two config words at `0xF30A0000`

Date: 2026-11-25 (measurement date). Written 2026-11-26.

This record covers one emulator change: a `0x10`-byte window at `0xF30A0000` with two read-back
(`CXD90045_F292_RF`) rows at relative offsets `+0x004` and `+0x008`. The scores below were produced
by this project's boot scorer (oracle version 3) on the scored build. This record does not claim any
physical camera result.

## Observation

Before the change, the baseline boot recorded 72 distinct unmodelled address pairs. Four of them were
the reads and writes at `0xF30A0004` and `0xF30A0008`. In that baseline boot the guest wrote `0x303`
to `+0x004` and read it back, and wrote `0x715` to `+0x008` and read it back; the first access to each
of those two offsets was a write. The four offsets `+0x010`, `+0x014`, `+0x018` and `+0x01C` were read
before their first write, so their reset value was not captured and they were left unmodelled.

## Change

The accepted diff does the following in the board file:

- **One window:** one `cxd90045_f292_create()` call at base `0xF30A0000`, size `0x10`, with its own
  rule table.
- **Two rows:** one `CXD90045_F292_RF` row at `+0x004` and one at `+0x008`, each noted as written and
  then read back.
- **Scope:** the window covers only `+0x00..+0x0F`. The existing `+0x1280` fill window and the rest of
  the page are unchanged.
- **Edges:** `+0x00` and `+0x0C` have no row, so the helper still logs them as unmodelled if touched.
- **Test:** a new static check (no QEMU boot) asserts the two rows, the single create call, the
  helper's fixed priority, that no row exists for `+0x010..+0x01C`, that the `F30A` defines are
  unchanged, and that the fill window is not overlapped.

## Result

Each column is one score file as recorded. No values are averaged.

| Measure | Before (baseline) | Rerun (scored) | Promotion re-measure |
| --- | --- | --- | --- |
| Identity check | ok | ok | ok |
| Final sequencer step | 42 of 43, `0x897F` | 42 of 43, `0x897F` | 42 of 43, `0x897F` |
| Mismatch count | 0 | 0 | 0 |
| Distinct unmodelled pairs | 72 | **68** | **68** |
| Unmodelled reads | 44 | 42 | 42 |
| Accesses to unmodelled space | 5387324 | 5434611 | 5055245 |
| `t_reach` | 51.5 / 46.37 s | 46.57 s (scored run only) | 46.61 / 51.54 s |
| Contended host | no | yes | no |

- **Rerun:** the scored round was contended (recorded maximum load1 4.4). Its two runs both reached
  step 42 with step value `0x897F`, but they disagreed on the final state at one request/report bit
  (bit 0, request `0x0102`, report `0x0118`). The furthest run was scored, at 68 distinct pairs.
- **Promotion re-measure:** two quiet runs, load1 1.34 and 1.39. Both ended at step 42 of 43 with step
  value `0x897F` and 0 mismatches, and both reported 68 distinct unmodelled pairs; `t_reach` was
  46.61 / 51.54 s.
- **Scorer verdict:** the comparison was accepted as coverage. The change removed four distinct pairs
  (72 to 68) with the same final machine state and no regression.

## Red team

The red-team verdict was upheld with correction, with 32 of 32 checks passing and no QEMU started.
It confirmed that:

- the first access to `+0x004` and to `+0x008` was a write, and the baseline did not touch `+0x00` or
  `+0x0C`;
- unlisted offsets in the window still log absolute `0x30a0000`-based addresses that the scorer
  matches, so the drop is exactly the four real pairs;
- only the intended `0xF30A0000/0x10` window overlaps the range, the `0xF30A0000/0x2000` fill
  container at priority -100 is transparent at `+0x0..+0xF`, and the new window at priority -600
  beats the -1000 catch-all;
- the scored top lost the four target pairs and kept `+0x010..+0x01C`.

The correction concerns the diff base only: main advanced after the lead branched, so a literal
`main..` diff also lists a test file the lead did not edit. The lead commit touches only the board
file and the new test, and the extra file's blob is identical at `HEAD^` and `HEAD`.

## Limits

- Emulator only. The record claims no physical camera result.
- The change adds coverage only. It does not move the firmware past its sequencer step, which stays
  at 42 of 43 with 0 mismatches.
- No driver or firmware code was analysed. The write-first reading comes from the baseline boot log,
  not from a bus capture or a datasheet. The read returns the value the guest itself wrote.
- The reset value of `+0x010..+0x01C` stays uncaptured and those offsets stay unmodelled.
- The baseline is not deterministic: in the scored rerun one of two runs showed the bit-0
  `0x0102`/`0x0118` disagreement, while the promotion's two quiet runs agreed.
- Timing is not a speed claim: the scored rerun was contended, so its `t_reach` is not judged.
- Not checked: whether a read of `+0x00` or `+0x0C` ever occurs. The baseline made no access to
  either edge.
