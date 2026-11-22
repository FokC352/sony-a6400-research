# E0678 — ILCE-7RM4A per-mode USB product ids are BK4 preset units, not compiled descriptors

Date: 2026-11-22. Offline packaged firmware only: no camera, no USB, no updater executed.
This record answers the open boundary of [E0646](0646-ilce-7rm4a-physical-usb-identity-and-getmodelinfo.md):
where does the a7R IVA's host-observed PC Remote PID `0x0D9F` come from, given that it is absent from
the 18 `normalUsbDescriptors` pairs of the packaged 1.13 `config.yaml` ([E0570](0570-ilce-7rm4a-offline-sourced-profile.md))?

## 0. What changes, in one paragraph

The stock USB service library `libInfraUsbCmn.so` exports `ptbl_did`, a 60-entry table of per-"device
id" (DID) records. Each non-null record names its own DID in word 0 and two **backup unit ids** in words
6 and 7. Resolving those units in the package's own BK4 backup preset, with the project's existing
decoder of the original `Data_SearchId` algorithm, gives the product ids directly: DID `0x00` → unit
`0x00E7002E` = **`0x0D9D`** (the host-observed Mass Storage PID) and DID `0x3B` → unit `0x00E70035` =
**`0x0D9F`** (the host-observed PC Remote PID). Three more DIDs carry `0x0D9C`, `0x0D9E` and `0x096F`.
So `0x0D9F` is preset data that the USB service overlays at runtime, which is why a scan of compiled
descriptors and of `config.yaml` does not find it. The second unit of every PID-bearing DID
except `0x37` is `0x00E7002C`, whose value is the only byte that differs between the package's two preset
families (`0x0100` vs `0x0200`); the host saw `REV_0200`, which is consistent with that unit being
`bcdDevice` (inference, section 5).

## 1. Inputs and tool identities

The owner's own updater was placed under the git-ignored `firmware/` directory and processed with the
procedure of `docs/ADDING_A_BIONZX_CAMERA.md` / E0506, with fresh output directories. Nothing was executed
from the package and nothing is committed.

| input | SHA-256 |
| --- | --- |
| `firmware/Update_ILCE7RM4AV113.exe` (369,269,800 B) | `545fdf6cf5009a4d077e8626313974c9ba5ee3741039c3d976c15d85b1246aa1` (= E0570) |
| restored cabinet `Update_ILCE7RM4AV113.cab` (368,793,670 B; `sony_updater_payload`: inverted MSCF at `0x71A2C`) | not separately pinned |
| `FirmwareData_ILCE7RM4AV113.dat` (365,374,952 B; only member extracted with `expand.exe -F:`) | `13e10d77347e4fd079d6ceca381e7c9c9dd74dd6de3ab5e44b4c9b6c8c78238b` |
| `config.yaml` (fwtool output; CRLF on Windows) | `bdca0815…cba32` as written; **`b511141a…2589f` after CRLF→LF**, equal to E0570 |
| `nflasha15/lib/libInfraUsbCmn.so` | `96638679c31ca8809fae2005fb992653629f040acad72280de10b2b1dcf8c246` |
| `0110_backup/SYSASTRA-DSLR/MGA/CX88900_UC2.bin` (BK4, named by `0110_backup/MainDest.txt`) | `7109822609da4ccddb139b67251373d364bb5b2399b0020f70b694f7971ae736` |
| `0110_backup/SYSASTRA-DSLR/MGA/CX88901_AP2.bin` (BK4, second family) | `311b99686f728209ad55701cb12ab553accadc848c24cca95d820597de166e03` |
| `nflasha3/av-cam.bin` (false-positive check only) | `44e0c17bde9477fca8e2b3b6c2b0c93f309491a78134a4ec230dceeecb9c49fa` (= E0570) |
| `fwtool.py` checkout (`/fwtool.py/`, git-ignored upstream) | `ma1co/fwtool.py` `cdba742b73eed5981480c326aeb30033aabf0223` |
| decoder | `src/a6400/execution/backup_service.py` `BK4PresetState` (original `Data_SearchId`, E0032/E0063) |
| new tool | `tools/research/usb_did_pid_trace.py` (this record) |
| host | Windows 11, Python 3.14.10, `venv` with pinned `requirements.txt` plus `pycryptodomex`/`pyyaml` for fwtool |

Commands, from the repository root:

```powershell
.\venv\Scripts\python.exe -m tools.research.sony_updater_payload firmware\Update_ILCE7RM4AV113.exe artifacts\analysis\ilce7rm4a-v113\payload
& "$env:SystemRoot\System32\expand.exe" '-F:FirmwareData_ILCE7RM4AV113.dat' artifacts\analysis\ilce7rm4a-v113\payload\Update_ILCE7RM4AV113.cab artifacts\analysis\ilce7rm4a-v113\cabinet
.\venv\Scripts\python.exe .\fwtool.py\fwtool.py unpack -f artifacts\analysis\ilce7rm4a-v113\cabinet\FirmwareData_ILCE7RM4AV113.dat -o firmware\extracted_ilce7rm4a_v113
.\venv\Scripts\python.exe -m tools.research.usb_did_pid_trace --usb-library <extract>\...\nflasha15_unpacked\lib\libInfraUsbCmn.so --preset <extract>\...\MGA\CX88900_UC2.bin --output-dir artifacts\e0678-ilce7rm4a-did-pid\cx88900_uc2
.\venv\Scripts\python.exe -m tools.research.usb_did_pid_trace --usb-library <same> --preset <extract>\...\MGA\CX88901_AP2.bin --output-dir artifacts\e0678-ilce7rm4a-did-pid\cx88901_ap2
```

Report SHA-256: `b9324f84791cc26f6987c85d904055055e9aeb8ea19ecd15dba72f2b5e174d57` (`cx88900_uc2`) and
`6824dc9589156fa494f47210016852bd24c6f6ee8906522867c24ac31089badb` (`cx88901_ap2`), kept under the
git-ignored `artifacts/`.

## 2. Negative results first

- **No static device descriptor carries the PID.** Across all 1,383 extracted files (1.35 GB), the
  little-endian pair `4C 05 9F 0D` (VID `0x054C`, PID `0x0D9F`, the device-descriptor layout) occurs
  nowhere, and neither does `4C 05 9D 0D` (Mass Storage) or `4C 05 A8 0C`/`4C 05 AA 0C`.
- **The compiled descriptor tables are runtime-filled.** `libInfraUsbCmn.so`'s ten `*_desc_tbl` symbols
  (including `remotepc_desc_tbl` at `0x1AD68` and `storage_desc_tbl` at `0x1AA40`) point at records
  whose descriptor pointers land at `0x1C000`, beyond the file-backed part of the second `PT_LOAD`
  (`0x1A440` + `0x2564`), i.e. in `.bss`. This matches E0159's a6400 note that the compiled
  `remotepc_desc_tbl` metadata does not carry the physical PID.
- **`av-cam.bin` is a false positive.** Its hit for `9E 0D 9F 0D` at `0x9230DA` sits in a monotone
  halfword run from `0x0C60` upward with sporadic gaps — a code table, not a PID table.

## 3. The DID table (original library)

`ptbl_did` is defined in `libInfraUsbCmn.so` at `0x1BFAC`, size 240 (60 words). The same library
imports `Backup_read`/`Backup_read_4Byte`/`Backup_get_datasize` from `libbackup.so` and carries
`DescriptorMaintainer::readPresetData`, `::Overwrite(CUSB_SVC_DID_INFO)`, `::OverwriteBcdUsb` and
`CUsbSvc::read_port_desc_from_backup`. Nine entries are non-null; every one of them has its own index in
word 0, which the tool checks and fails closed on otherwise.

## 4. Units resolved in the package's own preset

`BK4PresetState` validates the BK4 header and checksum and then resolves units exactly as the original
`Data_SearchId`. Both preset families give:

| DID | PID unit (word 6) | PID | aux unit (word 7) | aux `CX88900_*` | aux `CX88901_*` | host observation (E0646) |
| --- | --- | --- | --- | --- | --- | --- |
| `0x00` | `0x00E7002E` @ `0xB9CF6` | **`0x0D9D`** | `0x00E7002C` | `0x0100` | `0x0200` | Mass Storage `054C:0D9D`, `REV_0200` |
| `0x0B` | `0x00E7002F` @ `0xB9CF8` | `0x0D9E` | `0x00E7002C` | `0x0100` | `0x0200` | not observed |
| `0x0C` | `0x00E7002D` @ `0xB9CF4` | `0x0D9C` | `0x00E7002C` | `0x0100` | `0x0200` | not observed |
| `0x37` | `0x00E70032` @ `0xB9D3E` | `0x096F` | `0x00E70033` | `0x0100` | `0x0100` | not observed |
| `0x3B` | `0x00E70035` @ `0xB9D44` | **`0x0D9F`** | `0x00E7002C` | `0x0100` | `0x0200` | PC Remote `054C:0D9F` |
| `0x0D`, `0x38`, `0x39`, `0x3A` | none (`0xFFFFFFFF`) | — | none | — | — | — |

The neighbouring unit `0x00E70029` @ `0xB9CE0` holds the 16-byte string `DSC`, the SCSI INQUIRY product
string E0646 measured. All 28 `.bin` presets (14 `MGA`, 14 `MGA_HIBIKI2`) carry the block at the same
offsets; they fall into two byte-identical groups over `0xB9CC0..0xB9D60` (12 `*88900_*`, 16 `*88901_*`)
that differ **only** in the byte at `0xB9CF3`, the high byte of unit `0x00E7002C`.

## 5. What is measured, what is inferred

- **Measured:** every hash, symbol, address, record word, unit, offset and value above, from the named
  files with the named tools; the negative scans in section 2.
- **Inferred (by value match, not code):** DID `0x00` is Mass Storage and DID `0x3B` is PC Remote,
  because their preset PIDs equal the host-observed ones. Which mode DIDs `0x0B`, `0x0C` and `0x37` are
  is **not** determined; `0x0D9C` and `0x0D9E` are not claimed as MTP or any other mode.
- **Inferred:** unit `0x00E7002C` is `bcdDevice`. Support: it is paired with every PID-bearing DID except
  `0x37`, it is the only byte that differs between the preset families, and the host observed `REV_0200`.
  The alternative that `bcdDevice` comes from elsewhere is not excluded. If it is `bcdDevice`, the owner's
  body carries the `*88901_*` value although `MainDest.txt` names `CX88900_UC2.bin`; whether the updater
  rewrites the backup area at all, and which family a given body was built with, are not shown.
- **Not shown:** the runtime path from `ptbl_did` through `readPresetData`/`Overwrite` to the descriptor
  sent on the wire (no disassembly or execution was performed); that the live backup area holds these
  bytes; and why `config.yaml` lists `0x0D9C/0x0D9D` but not `0x0D9E`, `0x0D9F` or `0x096F`.

## 6. Superseded

E0646 §3 "where that descriptor comes from is not identified" and the matching `ilce_7rm4a` profile
boundary are superseded: `0x0D9F` is the BK4 preset unit `0x00E70035` that DID `0x3B` of the stock USB
service names. E0646's observations stand.

## 7. Repository changes

- `tools/research/usb_did_pid_trace.py`: decodes `ptbl_did` from the original library and resolves its
  units in a BK4 preset; fails closed on an unrecognised record layout or a non-file-backed read.
- `tests/test_usb_did_pid_trace.py`: constructed BK4 and DID fixtures only, no Sony bytes.
- `src/a6400/targets/bodies/ilce_7rm4a.py`: the PC Remote boundary and `usb` sources cite this record.
- Evidence index, README platform row and the rendered status SVG's receipt count. `STATUS_SNAPSHOT`
  stays on E9103 because `test_research_status_sync` pins it to the numerically highest record.
- Validation: full offline suite in the pinned `venv` (Windows, Python 3.14.10): 2466 run, OK, 279 skipped.

## 8. Next experiment

Running the same tool on another body's package (for example the a6400, whose observed PC Remote
`0x0CAA` is also absent from its packaged pairs, E0570 §4) would test whether the DID layout is shared.
Disassembling `DescriptorMaintainer::readPresetData`/`Overwrite` would replace the value-match mapping
of DIDs to modes with code evidence. Both are offline.
