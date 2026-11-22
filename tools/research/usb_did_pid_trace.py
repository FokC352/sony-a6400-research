"""Resolve per-mode USB product ids from the stock USB service and its BK4 preset.

Sony's ``libInfraUsbCmn.so`` exports ``ptbl_did``: one pointer per USB device id
(DID). Each non-null record starts with its own DID number and carries, at word 6
and word 7, two backup unit ids. ``DescriptorMaintainer`` reads those units through
``Backup_read`` and overwrites the compiled device descriptor with them, so the
product id a body presents is preset data, not a compiled descriptor constant.

This tool performs that lookup offline: it decodes the records from the original
library and resolves each unit with :class:`BK4PresetState`, which reproduces the
original ``Data_SearchId`` algorithm. It reads files only; it never touches a camera
or USB. Mapping a DID to a USB *mode* is not decided here: compare the resolved
values with host-observed identities and say which is which in the evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from project_paths import PROJECT_ROOT
from src.a6400.execution.backup_service import BK4PresetState

#: Unit id that marks "no backup override" in a DID record.
NO_UNIT = 0xFFFFFFFF
#: Word offsets inside a DID record (E0678 measured layout on ILCE-7RM4A 1.13).
RECORD_DID_WORD = 0
RECORD_PID_UNIT_WORD = 6
RECORD_AUX_UNIT_WORD = 7
RECORD_WORDS = 8


@dataclass(frozen=True)
class DidRecord:
    index: int
    record_va: int
    pid_unit: int
    aux_unit: int


def decode_did_records(read_words: Callable[[int, int], tuple[int, ...]],
                       table_va: int, entries: int) -> list[DidRecord]:
    """Decode the non-null DID records of a ``ptbl_did`` table.

    ``read_words(va, count)`` returns ``count`` little-endian words at ``va``. A
    record whose first word is not its own table index is rejected rather than
    guessed, so a layout change fails closed.
    """
    if entries <= 0:
        raise ValueError("ptbl_did must have at least one entry")
    records = []
    for index, record_va in enumerate(read_words(table_va, entries)):
        if not record_va:
            continue
        words = read_words(record_va, RECORD_WORDS)
        if words[RECORD_DID_WORD] != index:
            raise ValueError(f"DID record {index} at {record_va:#x} names DID "
                             f"{words[RECORD_DID_WORD]:#x}; layout not recognised")
        records.append(DidRecord(index, record_va, words[RECORD_PID_UNIT_WORD],
                                 words[RECORD_AUX_UNIT_WORD]))
    return records


def resolve_unit(state: BK4PresetState, unit: int) -> dict:
    """Resolve one backup unit to its preset offset and 16-bit value."""
    if unit == NO_UNIT:
        return {"unit": None, "status": "no_override"}
    desc = state.search_id(unit)
    if desc is None:
        return {"unit": f"{unit:#010x}", "status": "not_in_preset"}
    raw = state.read_unit(unit)
    row = {"unit": f"{unit:#010x}", "status": "resolved", "preset_offset": f"{desc.data_offset:#x}",
           "size": desc.size}
    if desc.size == 2:
        row["value"] = f"{struct.unpack('<H', raw)[0]:#06x}"
    else:
        row["status"] = "not_a_16_bit_unit"
    return row


def trace(records: list[DidRecord], state: BK4PresetState) -> list[dict]:
    return [{"did": f"{r.index:#04x}", "record_va": f"{r.record_va:#x}",
             "pid": resolve_unit(state, r.pid_unit), "aux": resolve_unit(state, r.aux_unit)}
            for r in records]


def elf_did_records(library: bytes) -> list[DidRecord]:
    """Decode ``ptbl_did`` straight out of the original shared library bytes."""
    import io
    from elftools.elf.elffile import ELFFile

    elf = ELFFile(io.BytesIO(library))
    loads = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]

    def read_words(va: int, count: int) -> tuple[int, ...]:
        for seg in loads:
            start, size = seg["p_vaddr"], seg["p_filesz"]
            if start <= va and va + 4 * count <= start + size:
                return struct.unpack_from(f"<{count}I", library, va - start + seg["p_offset"])
        raise ValueError(f"{va:#x}+{4 * count} is not file-backed; no zero bytes are assumed")

    dynsym = elf.get_section_by_name(".dynsym")
    matches = [s for s in dynsym.iter_symbols()
               if s.name == "ptbl_did" and s["st_shndx"] != "SHN_UNDEF"] if dynsym else []
    if len(matches) != 1 or matches[0]["st_size"] % 4:
        raise ValueError("Expected exactly one defined word-aligned ptbl_did symbol")
    return decode_did_records(read_words, matches[0]["st_value"], matches[0]["st_size"] // 4)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usb-library", type=Path, required=True, help="original libInfraUsbCmn.so")
    parser.add_argument("--preset", type=Path, required=True, help="original BK4 backup preset image")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    out = args.output_dir.resolve()
    if not out.is_relative_to(PROJECT_ROOT / "artifacts"):
        parser.error("Reports must remain under artifacts")
    library, preset = args.usb_library.read_bytes(), args.preset.read_bytes()
    report = {
        "scope": "Offline file read of a stock USB service library and BK4 preset; no camera or USB",
        "inputs": {"usb_library_sha256": hashlib.sha256(library).hexdigest(),
                   "preset_sha256": hashlib.sha256(preset).hexdigest()},
        "record_layout": {"did_word": RECORD_DID_WORD, "pid_unit_word": RECORD_PID_UNIT_WORD,
                          "aux_unit_word": RECORD_AUX_UNIT_WORD},
        "dids": trace(elf_did_records(library), BK4PresetState(preset)),
    }
    out.mkdir(parents=True, exist_ok=False)
    (out / "usb_did_pid_trace.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
