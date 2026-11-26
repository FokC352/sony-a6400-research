# E0707 - ILCE-6100 and ILCE-6400 firmware dump sets: integrity, identity and cross-body partitions

Date: 2026-11-26. **Strictly offline.** Local files only: no camera, USB, SD, flash,
service mode, `/dev/mem` or mode switch. No firmware bytes are published; the record
keeps names, sizes, hashes, build strings and directory names only.

## 1. Why

Two complete read-only partition sets now sit under the repository's git-ignored
`dumps/` directory. The map had the ILCE-6400 set from E0414/E0477 but no single record
of what each set contains, whether the files still match their manifests, and which
partitions are shared between the two bodies. This record closes that gap and fixes the
identity of the ILCE-6100 set at `dumps/a6100-dump-20261120/`.

## 2. Inputs

| Set | Contents | Image bytes |
| --- | --- | ---: |
| `dumps/a6100-dump-20261120/` | 19 `nflasha*.img`, `manifest.json`, `MD5SUMS`, `SHA256SUMS`, `partitions.txt` | 1,361,051,648 |
| `dumps/a6400-nflasha-readonly/` | 18 partitions in 22 `nflasha*.bin` files (four size-named verification copies), `nflasha-head-1m.bin`, `camera-page-0x03443000.bin`, `fs-setting.tar`, `fs-backup.tar`, `fs-etc-var.tar`, `manifest.json`, `manifest-final.json`, `SHA256SUMS.txt`, tree/inventory text | 1,360,986,112 + 1,048,576 head |

The a6100 `manifest.json` records five fetch sessions (`getvar x22, oem partition x1,
fetch ...`). Session 2 ended on `budget` and the fetch resumed; sessions 1, 3, 4 and 5
are `complete`. Every partition carries two passes from different sessions, and all 19
pairs have identical SHA-256 and MD5 values: `nflasha_head`, `nflasha1`-`nflasha7`,
`nflasha10`-`nflasha13` and `nflasha15` in sessions 1-2; `nflasha16`, `nflasha17`, `nflasha18` and
`nflasha23` in sessions 1 and 3; `nflashaB0`/`nflashaB1` in sessions 4-5.
Total 1,361,051,648 bytes = 1,298 MiB.

The a6400 set is the E0477 capture; `README-dump.md` states it is strictly read-only
(no `dd of=`, `flash_erase`, `nandwrite`, remount, ioctl, SD change or mode switch).

## 3. Integrity check

Method: recompute SHA-256 for every listed file and compare with the stored list
(`sha256sum -c SHA256SUMS`; on Windows, `Get-FileHash -Algorithm SHA256`). Signature
bytes were read with `xxd -l 64` and ext2 directories and `version.txt` with
`debugfs -R`.

| Set | Entries | Match | Exception |
| --- | ---: | ---: | --- |
| a6100 | 19 | 19 | none |
| a6400 | 39 | 38 | `manifest-final.json` |

The one a6400 exception is already recorded in E0524: the checksum list pins
`manifest-final.json` at `f4d3547d...`, while the file now hashes `6953ac0a...`. The
primary `manifest.json` (`735640d8...`), `SHA256SUMS.txt` (`95f0831e...`), all 18
partition images and the 1 MiB head read match their entries. Neither file was rewritten
here, and no stored hash was refreshed.

## 4. Identity of each set

| Fact | ILCE-6100 set | ILCE-6400 set |
| --- | --- | --- |
| Kernel banner in `nflasha3` | `Linux version 3.0.27_nl-rt106+ (jp10239@dic-build-scm-354) ... (Sony CE Linux 8.2.6.0) ... Tue Apr 6 04:11:46 JST 2021` | `... (jp10239@dic-build-scm-348) ... Sony CE Linux 8.2.6.0 ... Tue Aug 10 13:03:39 JST 2021` (E0415) |
| `nflasha7:/version.txt` | `38115_03.2021040601` | `38111_06.2021081003` |
| Model / version | ILCE-6100 Ver.1.00 (hash continuity with E9101) | ILCE-6400 |

`dumps/a6100-dump-20261120/nflasha3.img` hashes
`08379a20014c0e43bc4e3dc997a275b3f2d23aea5b1b8926d941b2a269d8a1df`, the value E9101
recorded for the ILCE-6100 Ver.1.00 build. That is the only link from this set to the
model name: the a6100 manifest carries no model or version field. Both bodies run the
same Sony CE Linux 8.2.6.0 release string but are distinct builds (different build host,
date, `version.txt` and every application partition below).

## 5. Partitions

Signatures are read from offset 0 (FAT boot sector with `0x55AA`, ext2 superblock magic
`0xEF53`, `WBI1`, `EXBL`) and are format identities, not decoded contents. `equal` means
the same partition is byte-identical in both sets.

| Partition | Bytes | Observed form | a6100 SHA-256 | Equal in a6400 |
| --- | ---: | --- | --- | --- |
| nflasha_head | 65,536 | starts `8246` + `1.00` (fastboot fetch header) | `0dd6c67e5a46551091e39558b0b57be5cf74756084689dd12028ba730433265d` | no such file |
| nflasha1 | 8,323,072 | FAT12 | `3807f2ed9837fb55017a32138cacd7772a0be5d408b96b59b9fac66aec25e6fd` | no |
| nflasha2 | 20,971,520 | FAT12 | `c3c5c5920bab4efe4652cf3dd72a8305ce7022cb09874dfda1faa8ba64d5b9cb` | no |
| nflasha3 | 50,331,648 | FAT12 | `08379a20014c0e43bc4e3dc997a275b3f2d23aea5b1b8926d941b2a269d8a1df` | no |
| nflasha4 | 12,582,912 | starts `CMMe` (`0x434d4d65`), format not decoded | `e8287b89f239c9b1e33062bb146e3869e45c4fe02737d0926903d0da9e7e7cfa` | no |
| nflasha5 | 62,914,560 | WBI1 | `e1951a176b57a020cb47d23cb47c419837743297a57df3dbfe387988b2b38251` | no |
| nflasha6 | 16,777,216 | FAT12 | `d13a03a90d773f659655928842f3f9e4438fd6590a5791e904ee06140468a399` | no |
| nflasha7 | 8,388,608 | ext2, 1,216 inodes / 4,844 blocks | `b11af4bde8a1780c90d9191cac87e2e7c8576de1ea5756e1aa1aaa249585c6a2` | no |
| nflasha10 | 12,582,912 | FAT12 | `45bc72264b15dc2b77e48eb20c89fa25dde5e5c276b737d5e80a6bce5981bbd6` | no |
| nflasha11 | 524,288,000 | FAT16, 223 nonzero bytes | `b77ec06f06b2162d2a169e461974266280f1aac31b64d426aeca19ea72beb840` | no |
| nflasha12 | 41,943,040 | FAT12, 178 nonzero bytes | `57097c29a17703f82a2f3c2cc741b1766af97684ab103acaee10a01eb1a96133` | no |
| nflasha13 | 4,194,304 | FAT12 | `b8444e6f76edc35a6d5b11f98b01ab414217754ec900911c590aee38e1918832` | no |
| nflasha15 | 314,572,800 | ext2, 52,832 inodes / 211,164 blocks | `63d8f46007637a6dd47ffa1a8de0b3c42842259b6357a5bc8b347d3120b0b1de` | no |
| nflasha16 | 146,800,640 | all zero (0 nonzero bytes) | `a6b881004b30e6eada284149d14f2502f26bf03fac68d98307f772d0648abc56` | **yes** |
| nflasha17 | 104,857,600 | FAT16, 179 nonzero bytes | `aa342be00074eef4f6d4aa4d36ccb108399bac5b8f7871d0798b76b6e02ec07a` | no |
| nflasha18 | 20,971,520 | FAT12 | `9ec4d63cea825daafd29050fda7d87011af3369323d2d270d322594a09c8029a` | no |
| nflasha23 | 2,097,152 | no filesystem signature; 2,072,824 nonzero bytes | `fc82a5d6e9779a55b311075a5dae5f056332f7c42e4fc61608cfc5279c6bdaf0` | **yes** |
| nflashaB0 | 4,194,304 | EXBL container | `5090ce72e0dbe19edb821ba0a81d54788478cdfbd799eec550f3f8fdbebad18a` | no |
| nflashaB1 | 4,194,304 | all zero (0 nonzero bytes) | `bb9f8df61474d25e71fa00722318cd387396ca1736605e1248821cc0de3d3af8` | **yes** |

## 6. Cross-body result

- The 18 common partitions have the **same size map**, 1,360,986,112 bytes in both sets;
  the a6100 set adds a 65,536-byte `nflasha_head` fetch.
- **Exactly three** common partitions are byte-identical: `nflasha16` (all zero),
  `nflasha23` (2,097,152 bytes) and `nflashaB1` (all zero). The other 15 differ,
  including `nflasha1` (`3807f2ed...` vs `b264e1e6...`), `nflasha5` (`e1951a17...` vs
  `a4724f7d...`), `nflasha7` (`b11af4bd...` vs `f65cb846...`) and `nflasha15`
  (`63d8f460...` vs `70d2613f...`).
- Both `nflasha7` ext2 images have 1,216 inodes and both `nflasha15` images have 52,832
  inodes, but the block counts differ (4,844 vs 4,840 and 211,164 vs 210,692).
- The `nflasha7` root directory carries the same 25 names on both bodies (27 entries
  including `.` and `..`): `bin`, `cert`, `dev`, `etc`, `initrd`, `kmod`, `lens`, `lib`,
  `log`, `mnt`, `network`, `proc`, `root`, `sbin`, `setting`, `sys`, `system`, `tmp`,
  `tmp_bt`, `tmp_cert`, `tmp_network`, `tmp_updater`, `usr`, `var`, `version.txt`. The
  a6100 adds `tmp_ipsec`. `nflasha15` carries the same 12 names on both (14 entries
  including `.` and `..`): `bin`, `codec`, `kmod`, `lib`, `local`, `sabin`, `sbin`,
  `scenario`, `share`, `tool`, `wifi-scripts`, `wlan`.

## 7. What this proves and does not prove

Proved: both local sets are complete for the partitions they list; the a6100 set passes
19/19 checksums with two agreeing passes per partition; the a6400 set's images pass
and only a known metadata summary file differs; the two bodies share three partitions
byte for byte while their application partitions are distinct builds.

Not proved: acquisition atomicity (the a6400 README marks mounted-rw partitions as
point-in-time snapshots, and the a6100 fetch does not record mount state); whole-device
coverage (neither set is a `/dev/nflasha` whole-disk image); partition roles on the
a6100 (its set has no `/proc/mounts`, so role names above come from a6400 evidence); and
any link between the shared `nflasha23` content and the ILCE-6400A MSC LUN 1 bytes,
which stay unattested (E0674).

## 8. Reproduction

```bash
# integrity (Linux); on Windows use Get-FileHash per file
(cd dumps/a6100-dump-20261120 && sha256sum -c SHA256SUMS)
(cd dumps/a6400-nflasha-readonly && sha256sum -c SHA256SUMS.txt)

# signatures and the ext2 build string (read-only)
xxd -l 64 dumps/a6100-dump-20261120/nflasha7.img
debugfs -R 'cat /version.txt' dumps/a6100-dump-20261120/nflasha7.img
debugfs -R 'cat /version.txt' dumps/a6400-nflasha-readonly/nflasha7.bin

# a6100 partition coverage + per-image SHA-256, via the emulator input preparer
python -m tools.research.qemu_boot_inputs_a6100 prepare \
  --dump dumps/a6100-dump-20261120 --report-dir artifacts/tests/e0707-a6100-inputs
```

The preparer refuses a hash mismatch, a size mismatch, or a gap/overlap in
`partitions.txt`, so a successful run also checks the partition map. Choose a new
`--report-dir` for each run; it writes a disposable `emmc.img` and never touches the
dump.

## 9. Limits and handling

The sets contain proprietary firmware and live device data (settings, serials, Wi-Fi
configuration). They are local inputs only: `dumps/` is git-ignored, must not be
committed, attached, uploaded or published, and only hashes, sizes and build strings
belong in records. The a6400 set's `manifest-final.json` mismatch is a metadata
inconsistency, not evidence that an image changed, and it must not be "fixed" by
refreshing the stored hash (E0524).
