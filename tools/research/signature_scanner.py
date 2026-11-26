#!/usr/bin/env python3
"""Fail-closed function & offset signature scanner across all 13 CXD90045 cameras.

Resolves Mod Loader v2 (``mlv2``), Menu Touch Full (``mtf2``), and core
``platform`` anchors using four deterministic static methods:
  1. ``elf_sym``       - exact ``.dynsym`` / ``.symtab`` symbol lookup
  2. ``elf_plt_got``   - ARM/Thumb ``.plt`` stub and ``.rel.dyn`` GOT slot lookup
  3. ``masked_thumb2`` - Thumb/Thumb-2 instruction-stream pattern + mask scan
  4. ``rodata_anchor`` - unique ``.rodata`` table prefix or C++ RTTI -> vtable chain

Fail-closed rule: an anchor is ``UNIQUE_VERIFIED`` only when it resolves to
exactly one site; zero matches return ``ABSENT`` and 2+ matches return
``AMBIGUOUS_REFUSED`` rather than picking the first hit.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import sys
from typing import Any

from project_paths import PROJECT_ROOT
from tools.research.elf32_lite import (
    Elf32Image,
    extract_masked_thumb2,
    scan_masked,
)

# Canonical 13 official CXD90045 firmware targets (from docs/evidence/cross_model/platform.tsv)
# plus ILCE-6400A (registered reference body for V1.01).
OFFICIAL_13_MODELS: tuple[str, ...] = (
    "ILCE-6400",
    "ILCE-6600",
    "ILCE-6100A",
    "ZV-E10",
    "ILCE-7M3",
    "ILCE-7C",
    "ILCE-7RM3",
    "ILCE-7RM4",
    "ILCE-7RM4A",
    "ILCE-9",
    "ILCE-9M2",
    "ZV-1",
    "DSC-RX0M2",
)

COMPANION_MODELS: tuple[str, ...] = ("ILCE-6400A",)

# Local workspace directory aliases -> canonical body model name.
LOCAL_DIR_ALIASES: dict[str, str] = {
    "extracted_a6400": "ILCE-6400",
    "a6400": "ILCE-6400",
    "extracted_a6400a_v101": "ILCE-6400A",
    "a6400a": "ILCE-6400A",
    "extracted_zve10_v203_linuxnames": "ZV-E10",
    "extracted_zve10_v203_packman": "ZV-E10",
    "zve10": "ZV-E10",
    "zv_e10": "ZV-E10",
    "ilce_6100a": "ILCE-6100A",
    "a6100a": "ILCE-6100A",
    "ilce_6600": "ILCE-6600",
    "a6600": "ILCE-6600",
    "ilce_7m3": "ILCE-7M3",
    "ilce_7c": "ILCE-7C",
    "ilce_7rm3": "ILCE-7RM3",
    "ilce_7rm4": "ILCE-7RM4",
    "ilce_7rm4a": "ILCE-7RM4A",
    "ilce_9": "ILCE-9",
    "ilce_9m2": "ILCE-9M2",
    "zv_1": "ZV-1",
    "dsc_rx0m2": "DSC-RX0M2",
}

RECT_PREFIX = bytes.fromhex(
    "110000005b004200670000005b004200bd0000005b004200"
    "130100005b004200690100005b004200bf0100005b004200"
)


@dataclass(frozen=True)
class SignatureSpec:
    name: str
    pack: str
    binary: str
    kind: str  # elf_sym | elf_plt_got | masked_thumb2 | rodata_anchor | rtti_vtable | bss_end | cave
    symbol: str = ""
    plt_mode: str = "plt"  # plt | got | got_and_plt
    pattern_hex: str = ""
    mask_hex: str = ""
    thumb_bit: int = 0
    structural: bool = False
    rodata_hex: str = ""
    slot_offset: int = 0
    expected_count: int = 1
    table_index: int = 0
    rtti_name: str = ""
    vtable_pick: str = "writable"  # writable (data.rel.ro / RW PT_LOAD)
    align_size: int = 8
    define_name: str = ""
    notes: str = ""


# Verified ground-truth expected ELF relative offsets / VAs (from mlv2 & mtf2 sources).
GROUND_TRUTH: dict[str, dict[str, int]] = {
    "ILCE-6400": {
        "mlv2.set_root": 0x005AB654,
        "mlv2.refresh": 0x005ABA80,
        "mlv2.open_item": 0x005AC044,
        "mlv2.set_text_plt": 0x0014F6D0,
        "mlv2.set_mode_plt": 0x00155A68,
        "mlv2.rect_tbl1_slot6": 0x00802A64,
        "mlv2.rect_tbl2_slot6": 0x00802AE4,
        "mlv2.v1_anchor": 0x00B78C40,
        "mlv2.event_new_plt": 0x000FD970,  # Thumb veneer entry (ARM body at +4 = 0xfd974)
        "mlv2.event_ctor": 0x00840FED,
        "mlv2.event_push": 0x00842599,
        "mlv2.event_mgr": 0x01424D70,
        "mlv2.cave_2048": 0x00002800,
        "mtf2.menu_binding": 0x005AB431,
        "mtf2.menu_enter_sel": 0x005AC045,
        "mtf2.menu_refresh": 0x005AC111,
        "mtf2.vt_menu_tab": 0x009391D0,
        "platform.im_osal_snd_msg": 0x00008FE4,
    },
    "ILCE-6400A": {
        "mlv2.set_root": 0x005AF8A4,
        "mlv2.refresh": 0x005AFCD0,
        "mlv2.open_item": 0x005B0294,
        "mtf2.menu_binding": 0x005AF681,
        "mtf2.menu_enter_sel": 0x005B0295,
        "mtf2.menu_refresh": 0x005B0361,
        "mtf2.vt_menu_tab": 0x0093A5B8,
        "platform.im_osal_snd_msg": 0x0000908C,
    },
}


BUILTIN_SPECS: tuple[SignatureSpec, ...] = (
    # ---- mlv2 (viewUnified2.so, libObj.so, libtestcmd.so) ----
    SignatureSpec(
        name="mlv2.set_root",
        pack="mlv2",
        binary="viewUnified2.so",
        kind="masked_thumb2",
        pattern_hex="044600af01b100f5c23303f52b73196000f000d0012084f8000298bd084698bd",
        mask_hex="ffffffff07fdffffffffffffffffffff00f800d0ffffffffffffffffffffffff",
        thumb_bit=0,
        define_name="MLV2_HOOK_SET_ROOT",
    ),
    SignatureSpec(
        name="mlv2.refresh",
        pack="mlv2",
        binary="viewUnified2.so",
        kind="masked_thumb2",
        pattern_hex="2de9004080b000af004600f1000040f0000300600060006000f000c000230060",
        mask_hex="ffff00c080ff00ff00ffe0fb000fe0fb000f00f800f800f800f800d0ffff00f8",
        thumb_bit=0,
        structural=True,
        define_name="MLV2_HOOK_REFRESH",
    ),
    SignatureSpec(
        name="mlv2.open_item",
        pack="mlv2",
        binary="viewUnified2.so",
        kind="masked_thumb2",
        pattern_hex="00f5c23303f52c732de9f0438bb00d460446196000af002600f000d0204600f000d0",
        mask_hex="ffffffffffffffffffffffffffffffffffffffffffffffff00f800d0ffff00f800d0",
        thumb_bit=0,
        define_name="MLV2_HOOK_OPEN_ITEM",
    ),
    SignatureSpec(
        name="mlv2.set_text_plt",
        pack="mlv2",
        binary="viewUnified2.so",
        kind="elf_plt_got",
        symbol="_ZN9GEN_Label13setTextDirectEPKc",
        plt_mode="plt",
        define_name="MLV2_PLT_SET_TEXT",
    ),
    SignatureSpec(
        name="mlv2.set_mode_plt",
        pack="mlv2",
        binary="viewUnified2.so",
        kind="elf_plt_got",
        symbol="_ZN2ux6wgtsys6Widget7setModeENS1_10WidgetModeE",
        plt_mode="plt",
        define_name="MLV2_PLT_SET_MODE",
    ),
    SignatureSpec(
        name="mlv2.rect_tbl1_slot6",
        pack="mlv2",
        binary="viewUnified2.so",
        kind="rodata_anchor",
        rodata_hex=RECT_PREFIX.hex(),
        slot_offset=len(RECT_PREFIX),
        expected_count=2,
        table_index=0,
        define_name="MLV2_RECT_TBL1_SLOT6",
    ),
    SignatureSpec(
        name="mlv2.rect_tbl2_slot6",
        pack="mlv2",
        binary="viewUnified2.so",
        kind="rodata_anchor",
        rodata_hex=RECT_PREFIX.hex(),
        slot_offset=len(RECT_PREFIX),
        expected_count=2,
        table_index=1,
        define_name="MLV2_RECT_TBL2_SLOT6",
    ),
    SignatureSpec(
        name="mlv2.v1_anchor",
        pack="mlv2",
        binary="viewUnified2.so",
        kind="bss_end",
        align_size=8,
        define_name="MLV2_V1_ANCHOR",
    ),
    SignatureSpec(
        name="mlv2.event_new_plt",
        pack="mlv2",
        binary="libObj.so",
        kind="elf_plt_got",
        symbol="_Znwj",
        plt_mode="plt",
        define_name="MLV2_LIBOBJ_EVENT_NEW_PLT",
    ),
    SignatureSpec(
        name="mlv2.event_ctor",
        pack="mlv2",
        binary="libObj.so",
        kind="elf_sym",
        symbol="_ZN5EventC1Emhh",
        define_name="MLV2_LIBOBJ_EVENT_CTOR",
    ),
    SignatureSpec(
        name="mlv2.event_push",
        pack="mlv2",
        binary="libObj.so",
        kind="elf_sym",
        symbol="_ZN12EventManager4pushEP5Eventb",
        define_name="MLV2_LIBOBJ_EVENT_PUSH",
    ),
    SignatureSpec(
        name="mlv2.event_mgr",
        pack="mlv2",
        binary="libObj.so",
        kind="elf_sym",
        symbol="g_pEvtMgr",
        define_name="MLV2_LIBOBJ_EVENT_MGR",
    ),
    SignatureSpec(
        name="mlv2.event_mgr_got",
        pack="mlv2",
        binary="libObj.so",
        kind="elf_plt_got",
        symbol="g_pEvtMgr",
        plt_mode="glob_dat",
        define_name="MLV2_LIBOBJ_EVENT_MGR_GOT",
    ),
    SignatureSpec(
        name="mlv2.cave_2048",
        pack="mlv2",
        binary="libtestcmd.so",
        kind="cave",
        align_size=2048,
        define_name="MLV2_SHIM_CAVE_OFF",
    ),
    # ---- mtf2 (menu_touch_full anchors) ----
    SignatureSpec(
        name="mtf2.menu_binding",
        pack="mtf2",
        binary="viewUnified2.so",
        kind="masked_thumb2",
        pattern_hex="b0b500f5c2358ab005f52b75044600af4ff0ff33286807f124017b623b62fb61",
        mask_hex="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        thumb_bit=1,
        define_name="MTF2_MENU_BINDING_OFF",
    ),
    SignatureSpec(
        name="mtf2.menu_enter_sel",
        pack="mtf2",
        binary="viewUnified2.so",
        kind="masked_thumb2",
        pattern_hex="00f5c23303f52c732de9f0438bb00d460446196000af002600f000d0204600f000d0",
        mask_hex="ffffffffffffffffffffffffffffffffffffffffffffffff00f800d0ffff00f800d0",
        thumb_bit=1,
        define_name="MTF2_MENU_ENTER_SEL_OFF",
    ),
    SignatureSpec(
        name="mtf2.menu_refresh",
        pack="mtf2",
        binary="viewUnified2.so",
        kind="masked_thumb2",
        pattern_hex="00f5c23303f52a73b0b504461a7800af0d4602b100221a7000f000d02046",
        mask_hex="ffffffffffffffffffffffffffffffffffff07fdffffffff00f800d0ffff",
        thumb_bit=1,
        define_name="MTF2_MENU_REFRESH_OFF",
    ),
    SignatureSpec(
        name="mtf2.vt_menu_tab",
        pack="mtf2",
        binary="viewUnified2.so",
        kind="rtti_vtable",
        rtti_name="15PAS_MenuListTab",
        define_name="MTF2_VT_MENU_TAB_OFF",
    ),
    SignatureSpec(
        name="mtf2.vt_menu_popup",
        pack="mtf2",
        binary="viewUnified2.so",
        kind="rtti_vtable",
        rtti_name="17PAS_MenuListPopup",
        define_name="MTF2_VT_MENU_POPUP_OFF",
    ),
    SignatureSpec(
        name="mtf2.ev_ctor",
        pack="mtf2",
        binary="libObj.so",
        kind="elf_sym",
        symbol="_ZN5EventC1Emhh",
        define_name="MTF2_EV_CTOR_OFF",
    ),
    SignatureSpec(
        name="mtf2.evmgr_push",
        pack="mtf2",
        binary="libObj.so",
        kind="elf_sym",
        symbol="_ZN12EventManager4pushEP5Eventb",
        define_name="MTF2_EVMGR_PUSH_OFF",
    ),
    SignatureSpec(
        name="mtf2.evmgr_slot",
        pack="mtf2",
        binary="libObj.so",
        kind="elf_sym",
        symbol="g_pEvtMgr",
        define_name="MTF2_EVMGR_SLOT_OFF",
    ),
    # ---- platform ----
    SignatureSpec(
        name="platform.im_osal_snd_msg",
        pack="platform",
        binary="im.elf",
        kind="elf_sym",
        symbol="osal_snd_msg",
        define_name="PLAT_IM_OSAL_SND_MSG",
    ),
    SignatureSpec(
        name="platform.libobj_osal_snd_msg_plt",
        pack="platform",
        binary="libObj.so",
        kind="elf_plt_got",
        symbol="osal_snd_msg",
        plt_mode="plt",
        define_name="PLAT_LIBOBJ_OSAL_SND_MSG_PLT",
    ),
    SignatureSpec(
        name="platform.libobj_event_push",
        pack="platform",
        binary="libObj.so",
        kind="elf_sym",
        symbol="_ZN12EventManager4pushEP5Eventb",
        define_name="PLAT_LIBOBJ_EVENT_PUSH",
    ),
)


@dataclass
class AnchorResult:
    name: str
    pack: str
    binary: str
    kind: str
    status: str  # UNIQUE_VERIFIED | AMBIGUOUS_REFUSED | ABSENT | MISSING_BINARY
    value: int | None = None
    match_count: int = 0
    matches: list[int] = field(default_factory=list)
    define_name: str = ""
    detail: str = ""
    ground_truth: str | None = None  # MATCH | MISMATCH | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pack": self.pack,
            "binary": self.binary,
            "kind": self.kind,
            "status": self.status,
            "value": f"0x{self.value:08x}" if self.value is not None else None,
            "value_int": self.value,
            "match_count": self.match_count,
            "matches": [f"0x{m:08x}" for m in self.matches[:8]],
            "define_name": self.define_name,
            "detail": self.detail,
            "ground_truth": self.ground_truth,
        }


def _find_binary_in_root(body_root: Path, filename: str) -> Path | None:
    preferred = (
        body_root / "firmware.tar_unpacked" / "0700_part_image" / "dev" / "nflasha15_unpacked" / "lib" / filename,
        body_root / "firmware.tar_unpacked" / "0700_part_image" / "dev" / "nflasha15_unpacked" / "bin" / filename,
        body_root / "lib" / filename,
        body_root / "usr" / "lib" / filename,
        body_root / "bin" / filename,
        body_root / filename,
    )
    for cand in preferred:
        if cand.is_file():
            return cand
    nflash15 = body_root / "firmware.tar_unpacked" / "0700_part_image" / "dev" / "nflasha15_unpacked"
    if nflash15.is_dir():
        hits = sorted(nflash15.rglob(filename))
        if hits:
            return hits[0]
    hits = [
        p
        for p in sorted(body_root.rglob(filename))
        if p.is_file() and "nflasha1_unpacked" not in p.parts
    ]
    return hits[0] if hits else None


def discover_body_roots(roots: list[Path]) -> tuple[dict[str, Path], list[dict[str, str]]]:
    """Map discovered unpacked directories under ``roots`` to canonical model names."""
    discovered: dict[str, Path] = {}
    notes: list[dict[str, str]] = []
    # Prefer deterministic priority when multiple local trees map to same body
    # (e.g. extracted_zve10_v203_linuxnames before extracted_zve10_v203 raw container).
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            key = child.name.lower()
            model = LOCAL_DIR_ALIASES.get(key)
            if model is None:
                upper = child.name.upper()
                if upper in OFFICIAL_13_MODELS or upper in COMPANION_MODELS:
                    model = upper
            has_elfs = _find_binary_in_root(child, "viewUnified2.so") is not None or _find_binary_in_root(child, "im.elf") is not None
            if model is None:
                notes.append({"dir": str(child), "status": "UNMAPPED_OR_NON_CXD90045"})
                continue
            if not has_elfs:
                notes.append({"dir": str(child), "model": model, "status": "UNEXTRACTED_CONTAINER"})
                continue
            if model not in discovered or "linuxnames" in child.name.lower():
                discovered[model] = child
    return discovered, notes


def _eval_spec(
    spec: SignatureSpec,
    img: Elf32Image | None,
    *,
    model_name: str,
    sym_cache: dict[str, dict[str, list[int]]],
    plt_cache: dict[str, dict[str, list[int]]],
    rel_cache: dict[str, dict[str, list[int]]],
) -> AnchorResult:
    if img is None:
        return AnchorResult(
            name=spec.name,
            pack=spec.pack,
            binary=spec.binary,
            kind=spec.kind,
            status="MISSING_BINARY",
            define_name=spec.define_name,
            detail=f"{spec.binary} absent in unpacked rootfs",
        )

    bkey = str(img.path or spec.binary)
    status = "ABSENT"
    matches: list[int] = []
    value: int | None = None
    detail = ""

    if spec.kind == "elf_sym":
        if bkey not in sym_cache:
            by_name: dict[str, list[int]] = {}
            for s in img.symbols():
                if s.address:
                    by_name.setdefault(s.name, []).append(s.address)
            sym_cache[bkey] = by_name
        hits = sym_cache[bkey].get(spec.symbol, [])
        # Deduplicate identical addresses from .dynsym + .symtab if any.
        matches = sorted(set(hits))
        if len(matches) == 1:
            status = "UNIQUE_VERIFIED"
            value = matches[0]
            detail = f"symbol {spec.symbol}"
        elif len(matches) > 1:
            status = "AMBIGUOUS_REFUSED"
            detail = f"{len(matches)} distinct addresses for {spec.symbol}"
        else:
            status = "ABSENT"
            detail = f"symbol {spec.symbol} not found"

    elif spec.kind == "elf_plt_got":
        if bkey not in plt_cache:
            p_by_name: dict[str, list[int]] = {}
            for va, name in img.plt_map().items():
                p_by_name.setdefault(name, []).append(va)
            plt_cache[bkey] = p_by_name
        if bkey not in rel_cache:
            r_by_name: dict[str, list[tuple[int, int]]] = {}
            for rel in img.relocations((".rel.plt", ".rel.dyn")):
                if rel.symbol_name:
                    r_by_name.setdefault(rel.symbol_name, []).append((rel.r_offset, rel.r_type))
            rel_cache[bkey] = r_by_name

        if spec.plt_mode == "glob_dat":
            hits = sorted({off for off, rtype in rel_cache[bkey].get(spec.symbol, []) if rtype == 21})
            kind_lbl = "GOT GLOB_DAT reloc"
        elif spec.plt_mode == "got":
            hits = sorted({off for off, _ in rel_cache[bkey].get(spec.symbol, [])})
            kind_lbl = "GOT reloc"
        else:
            hits = sorted(set(plt_cache[bkey].get(spec.symbol, [])))
            kind_lbl = "PLT stub"
        matches = hits
        if len(matches) == 1:
            status = "UNIQUE_VERIFIED"
            value = matches[0]
            detail = f"{kind_lbl} for {spec.symbol}"
        elif len(matches) > 1:
            status = "AMBIGUOUS_REFUSED"
            detail = f"{len(matches)} {kind_lbl} hits for {spec.symbol}"
        else:
            detail = f"no {kind_lbl} for {spec.symbol}"

    elif spec.kind == "masked_thumb2":
        pat = bytes.fromhex(spec.pattern_hex)
        msk = bytes.fromhex(spec.mask_hex)
        raw_hits: list[int] = []
        for sec in img.sections:
            if sec.sh_type == 1 and (sec.sh_flags & 0x4) and sec.sh_size >= len(pat):
                sec_bytes = img.section_bytes(sec)
                for rel_off in scan_masked(sec_bytes, pat, msk, step=2):
                    raw_hits.append((sec.sh_addr + rel_off) | (1 if spec.thumb_bit else 0))
        matches = sorted(set(raw_hits))
        if len(matches) == 1:
            status = "UNIQUE_VERIFIED"
            value = matches[0]
            detail = f"masked_thumb2 ({len(pat)}B, structural={spec.structural})"
        elif len(matches) > 1:
            status = "AMBIGUOUS_REFUSED"
            detail = f"{len(matches)} masked_thumb2 hits"
        else:
            detail = "masked_thumb2 pattern not found"

    elif spec.kind == "rodata_anchor":
        prefix = bytes.fromhex(spec.rodata_hex)
        ro_hits: list[int] = []
        pos = 0
        while True:
            idx = img.data.find(prefix, pos)
            if idx < 0:
                break
            va = img.offset_to_va(idx)
            if va is not None:
                ro_hits.append(va + spec.slot_offset)
            pos = idx + 1
        matches = ro_hits
        if len(matches) == spec.expected_count and 0 <= spec.table_index < len(matches):
            status = "UNIQUE_VERIFIED"
            value = matches[spec.table_index]
            detail = f"rodata table {spec.table_index + 1}/{spec.expected_count}"
        elif matches:
            status = "AMBIGUOUS_REFUSED"
            detail = f"expected {spec.expected_count} rodata tables, found {len(matches)}"
        else:
            detail = "rodata prefix not found"

    elif spec.kind == "bss_end":
        bss = img.section(".bss")
        loads = img.load_segments()
        if bss is not None and loads:
            rw = max(loads, key=lambda s: s.p_vaddr)
            rw_end = bss.sh_addr + bss.sh_size
            page_end = (rw_end + 0xFFF) & ~0xFFF
            if rw_end + spec.align_size <= page_end:
                status = "UNIQUE_VERIFIED"
                value = rw_end
                matches = [rw_end]
                detail = f".bss 0x{bss.sh_addr:x}+0x{bss.sh_size:x}, {page_end - rw_end}B tail"
            else:
                detail = f"insufficient .bss tail before 0x{page_end:x}"
        else:
            detail = "missing .bss or RW segment"

    elif spec.kind == "cave":
        loads = img.load_segments()
        rx = [s for s in loads if s.p_flags & 0x1]
        rw = [s for s in loads if (s.p_flags & 0x2) and not (s.p_flags & 0x1)]
        if rx:
            rx0 = rx[0]
            min_end = max(rx0.p_vaddr + rx0.p_filesz, len(img.data))
            cave_va = (min_end + 0xFF) & ~0xFF
            page_end = (rx0.p_vaddr + rx0.p_filesz + 0xFFF) & ~0xFFF
            if cave_va + spec.align_size <= page_end and (not rw or cave_va < rw[0].p_vaddr):
                status = "UNIQUE_VERIFIED"
                value = cave_va
                matches = [cave_va]
                detail = f"RX page tail after EOF 0x{len(img.data):x} (page_end=0x{page_end:x})"
            else:
                detail = f"RX cave 0x{cave_va:x} exceeds page end 0x{page_end:x}"
        else:
            detail = "no executable PT_LOAD segment"

    elif spec.kind == "rtti_vtable":
        target = spec.rtti_name.encode("ascii") + b"\x00"
        name_off = img.data.find(target)
        if name_off < 0:
            detail = f"RTTI name {spec.rtti_name!r} absent"
        else:
            name_va = img.offset_to_va(name_off)
            writable_secs = [
                sec
                for sec in img.sections
                if sec.sh_type != 8 and not (sec.sh_flags & 0x4) and (sec.sh_flags & 0x2) and sec.sh_size >= 4
            ]
            ti_vas: list[int] = []
            for sec in writable_secs:
                blob = img.section_bytes(sec)
                for off in range(0, len(blob) - 3, 4):
                    if struct.unpack_from("<I", blob, off)[0] == name_va:
                        ti_vas.append(sec.sh_addr + off - 4)
            vtable_vas: list[int] = []
            ti_set = set(ti_vas)
            if ti_set:
                for sec in writable_secs:
                    if sec.name.startswith(".rel"):
                        continue
                    blob = img.section_bytes(sec)
                    for off in range(0, len(blob) - 3, 4):
                        if struct.unpack_from("<I", blob, off)[0] in ti_set:
                            vtable_vas.append(sec.sh_addr + off + 4)
            matches = sorted(set(vtable_vas))
            if len(matches) == 1:
                status = "UNIQUE_VERIFIED"
                value = matches[0]
                detail = f"RTTI {spec.rtti_name!r} -> typeinfo 0x{next(iter(ti_set)):x}"
            elif len(matches) > 1:
                status = "AMBIGUOUS_REFUSED"
                detail = f"{len(matches)} vtables reference RTTI {spec.rtti_name!r}"
            else:
                detail = f"no vtable pointer found for RTTI {spec.rtti_name!r}"

    gt_status = None
    expected_gt = GROUND_TRUTH.get(model_name, {}).get(spec.name)
    if expected_gt is not None:
        gt_status = "MATCH" if value == expected_gt else f"MISMATCH(expected=0x{expected_gt:08x})"

    return AnchorResult(
        name=spec.name,
        pack=spec.pack,
        binary=spec.binary,
        kind=spec.kind,
        status=status,
        value=value,
        match_count=len(matches),
        matches=matches,
        define_name=spec.define_name,
        detail=detail,
        ground_truth=gt_status,
    )


def scan_body(
    model_name: str,
    body_root: Path,
    specs: tuple[SignatureSpec, ...] | list[SignatureSpec],
) -> dict[str, Any]:
    """Scan all signatures in ``specs`` against one unpacked body directory."""
    needed_bins = sorted({s.binary for s in specs})
    images: dict[str, Elf32Image | None] = {}
    binary_hashes: dict[str, dict[str, Any]] = {}
    for bname in needed_bins:
        bpath = _find_binary_in_root(body_root, bname)
        if bpath is None:
            images[bname] = None
            binary_hashes[bname] = {"present": False}
        else:
            img = Elf32Image.from_file(bpath)
            images[bname] = img
            binary_hashes[bname] = {
                "present": True,
                "path": str(bpath),
                "sha256": img.sha256,
                "size": len(img.data),
            }

    sym_cache: dict[str, dict[str, list[int]]] = {}
    plt_cache: dict[str, dict[str, list[int]]] = {}
    rel_cache: dict[str, dict[str, list[int]]] = {}

    results = [
        _eval_spec(
            spec,
            images.get(spec.binary),
            model_name=model_name,
            sym_cache=sym_cache,
            plt_cache=plt_cache,
            rel_cache=rel_cache,
        )
        for spec in specs
    ]

    counts = {
        "UNIQUE_VERIFIED": sum(1 for r in results if r.status == "UNIQUE_VERIFIED"),
        "AMBIGUOUS_REFUSED": sum(1 for r in results if r.status == "AMBIGUOUS_REFUSED"),
        "ABSENT": sum(1 for r in results if r.status == "ABSENT"),
        "MISSING_BINARY": sum(1 for r in results if r.status == "MISSING_BINARY"),
    }
    gt_mismatches = [
        r.name for r in results if r.ground_truth is not None and r.ground_truth != "MATCH"
    ]
    return {
        "model": model_name,
        "availability": "UNPACKED_PRESENT",
        "root": str(body_root),
        "binaries": binary_hashes,
        "counts": counts,
        "ground_truth_mismatches": gt_mismatches,
        "anchors": [r.to_dict() for r in results],
    }


def scan_roots(
    roots: list[Path],
    *,
    pack: str = "all",
    extra_specs: list[SignatureSpec] | None = None,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Scan all 13 official CXD90045 bodies (+ companion bodies present under ``roots``)."""
    specs = [s for s in BUILTIN_SPECS if pack == "all" or s.pack == pack]
    if extra_specs:
        specs.extend(extra_specs)

    discovered, discovery_notes = discover_body_roots(roots)
    all_models: list[str] = list(OFFICIAL_13_MODELS)
    for model in sorted(discovered):
        if model not in all_models:
            all_models.append(model)

    bodies_out: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, max(1, len(discovered))))) as pool:
        futures = {
            model: pool.submit(scan_body, model, root, specs)
            for model, root in discovered.items()
        }
        for model in all_models:
            if model in futures:
                bodies_out[model] = futures[model].result()
            else:
                bodies_out[model] = {
                    "model": model,
                    "availability": "NOT_UNPACKED_LOCALLY",
                    "root": None,
                    "binaries": {},
                    "counts": {
                        "UNIQUE_VERIFIED": 0,
                        "AMBIGUOUS_REFUSED": 0,
                        "ABSENT": 0,
                        "MISSING_BINARY": len(specs),
                    },
                    "ground_truth_mismatches": [],
                    "anchors": [],
                }

    return {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "pack": pack,
        "official_13_models": list(OFFICIAL_13_MODELS),
        "companion_models": [m for m in all_models if m not in OFFICIAL_13_MODELS],
        "unpacked_present": [m for m in all_models if bodies_out[m]["availability"] == "UNPACKED_PRESENT"],
        "not_unpacked_locally": [
            m for m in all_models if bodies_out[m]["availability"] != "UNPACKED_PRESENT"
        ],
        "discovery_notes": discovery_notes,
        "bodies": bodies_out,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a clean cross-body matrix report in Markdown."""
    lines = [
        "# CXD90045 13-Camera Function & Offset Signature Matrix",
        "",
        f"- **Generated:** `{report['generated_utc']}`",
        f"- **Pack:** `{report['pack']}`",
        f"- **Unpacked locally ({len(report['unpacked_present'])}):** {', '.join(f'`{m}`' for m in report['unpacked_present']) or 'none'}",
        f"- **Not unpacked locally ({len(report['not_unpacked_locally'])}):** {', '.join(f'`{m}`' for m in report['not_unpacked_locally']) or 'none'}",
        "",
        "## Body Status Summary",
        "",
        "| Model | Availability | Verified | Ambiguous | Absent | Missing Bin | GT Mismatches |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model, info in report["bodies"].items():
        c = info["counts"]
        lines.append(
            f"| `{model}` | {info['availability']} | {c['UNIQUE_VERIFIED']} | "
            f"{c['AMBIGUOUS_REFUSED']} | {c['ABSENT']} | {c['MISSING_BINARY']} | "
            f"{len(info['ground_truth_mismatches'])} |"
        )

    present = [info for info in report["bodies"].values() if info["availability"] == "UNPACKED_PRESENT"]
    if present:
        lines.extend(["", "## Resolved Anchors (Unpacked Bodies)", ""])
        anchor_names = [a["name"] for a in present[0]["anchors"]]
        header = "| Anchor | " + " | ".join(f"`{b['model']}`" for b in present) + " |"
        sep = "| --- | " + " | ".join("---" for _ in present) + " |"
        lines.extend([header, sep])
        for idx, aname in enumerate(anchor_names):
            row = [f"`{aname}`"]
            for body in present:
                anc = body["anchors"][idx]
                if anc["status"] == "UNIQUE_VERIFIED":
                    cell = f"`{anc['value']}`"
                    if anc["ground_truth"] == "MATCH":
                        cell += " (GT)"
                else:
                    cell = anc["status"]
                row.append(cell)
            lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    return "\n".join(lines)


def emit_c_headers(report: dict[str, Any], out_dir: Path) -> list[Path]:
    """Emit one C header per unpacked body defining verified offsets and guards."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for model, info in report["bodies"].items():
        if info["availability"] != "UNPACKED_PRESENT":
            continue
        slug = model.lower().replace("-", "_")
        header_path = out_dir / f"{slug}_signatures.h"
        lines = [
            "/* Auto-generated by tools.research.signature_scanner (fail-closed). */",
            "#pragma once",
            "#include <stdint.h>",
            "",
            f"/* Model: {model} */",
        ]
        for bname, binfo in sorted(info["binaries"].items()):
            if binfo.get("present"):
                macro = "SIG_SHA256_" + bname.upper().replace(".", "_")
                lines.append(f'#define {macro} "{binfo["sha256"]}"')
        lines.append("")
        for anc in info["anchors"]:
            dname = anc["define_name"] or anc["name"].upper().replace(".", "_")
            if anc["status"] == "UNIQUE_VERIFIED" and anc["value_int"] is not None:
                lines.append(f"#define {dname} 0x{anc['value_int']:08x}u /* {anc['kind']} */")
            else:
                lines.append(f"/* UNRESOLVED {dname}: {anc['status']} ({anc['detail']}) */")
        lines.append("")
        header_path.write_text("\n".join(lines), encoding="utf-8")
        written.append(header_path)
    return written


def extract_signature_command(elf_path: Path, va: int, insn_count: int, structural: bool) -> dict[str, Any]:
    """Extract a masked Thumb-2 signature JSON snippet from ``elf_path`` at ``va``."""
    img = Elf32Image.from_file(elf_path)
    off = img.va_to_offset(va)
    if off is None:
        raise ValueError(f"VA 0x{va:08x} is not backed by {elf_path}")
    pat, msk = extract_masked_thumb2(img.data, off, insn_count=insn_count, structural=structural)
    hits: list[str] = []
    for sec in img.sections:
        if sec.sh_type == 1 and (sec.sh_flags & 0x4) and sec.sh_size >= len(pat):
            for rel_off in scan_masked(img.section_bytes(sec), pat, msk, step=2):
                hits.append(f"0x{(sec.sh_addr + rel_off) | (va & 1):08x}")
    return {
        "binary": elf_path.name,
        "sha256": img.sha256,
        "va": f"0x{va:08x}",
        "insns": insn_count,
        "structural": structural,
        "pattern_hex": pat.hex(),
        "mask_hex": msk.hex(),
        "self_hits": hits,
        "unique_in_source": len(hits) == 1,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", type=Path, action="append", help="Firmware unpacked root(s)")
    parser.add_argument("--pack", choices=("all", "mlv2", "mtf2", "platform"), default="all")
    parser.add_argument("--json", type=Path, default=None, help="Write JSON report")
    parser.add_argument("--markdown", type=Path, default=None, help="Write Markdown matrix")
    parser.add_argument("--emit-c-header", type=Path, default=None, help="Directory for C headers")
    parser.add_argument("--extract", action="store_true", help="Extract masked_thumb2 signature")
    parser.add_argument("--elf", type=Path, default=None, help="Source ELF for --extract")
    parser.add_argument("--va", type=lambda v: int(v, 0), default=None, help="Function VA for --extract")
    parser.add_argument("--insns", type=int, default=12, help="Instruction count for --extract")
    parser.add_argument("--structural", action="store_true", help="Relax stack/struct offsets")
    parser.add_argument("--workers", type=int, default=4, help="Parallel worker count")
    args = parser.parse_args(argv)

    if args.extract:
        if args.elf is None or args.va is None:
            raise SystemExit("--extract requires --elf and --va")
        spec = extract_signature_command(args.elf, args.va, args.insns, args.structural)
        print(json.dumps(spec, indent=2))
        return 0 if spec["unique_in_source"] else 2

    roots = args.roots if args.roots else [PROJECT_ROOT / "firmware"]
    report = scan_roots(roots, pack=args.pack, max_workers=args.workers)
    md = render_markdown(report)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(md, encoding="utf-8")
    if args.emit_c_header:
        emit_c_headers(report, args.emit_c_header)

    if not args.json and not args.markdown:
        print(md)
    else:
        summary = {
            m: info["counts"]
            for m, info in report["bodies"].items()
            if info["availability"] == "UNPACKED_PRESENT"
        }
        print(json.dumps({"unpacked": summary, "not_unpacked_count": len(report["not_unpacked_locally"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
