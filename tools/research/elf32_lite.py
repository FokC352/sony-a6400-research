"""Pure-Python stdlib ELF32 and Thumb-2 static analysis helpers.

Provides deterministic ELF32 parsing (segments, sections, .dynsym/.symtab,
ARM/Thumb PLT/GOT relocations, strings) and Thumb-2 instruction decoding,
direct-call extraction, and instruction masking without native C extensions.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
import hashlib
import re
import struct
from pathlib import Path
from typing import NamedTuple

_ELF_MAGIC = b"\x7fELF"
_PT_LOAD = 1
_SHT_SYMTAB = 2
_SHT_STRTAB = 3
_SHT_RELA = 4
_SHT_REL = 9
_SHT_DYNSYM = 11
_SHF_ALLOC = 0x2
_SHF_EXECINSTR = 0x4
_STT_NOTYPE = 0
_STT_OBJECT = 1
_STT_FUNC = 2
_STB_LOCAL = 0
_ASCII_RUN_RE = re.compile(rb"[\x20-\x7e]{4,}")


class ElfSymbol(NamedTuple):
    """Decoded ELF32 symbol matching tools.research.elf_symbols.Symbol fields."""

    name: str
    address: int
    size: int
    type: str
    source: str


@dataclass(frozen=True)
class ElfSegment:
    p_type: int
    p_offset: int
    p_vaddr: int
    p_paddr: int
    p_filesz: int
    p_memsz: int
    p_flags: int


@dataclass(frozen=True)
class ElfSection:
    index: int
    name: str
    sh_type: int
    sh_flags: int
    sh_addr: int
    sh_offset: int
    sh_size: int
    sh_link: int
    sh_info: int
    sh_entsize: int


@dataclass(frozen=True)
class ElfReloc:
    section: str
    r_offset: int
    r_info: int
    r_type: int
    sym_index: int
    symbol_name: str
    symbol_value: int


def _read_cstr(blob: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(blob):
        return ""
    end = blob.find(b"\x00", offset)
    if end < 0:
        end = len(blob)
    return blob[offset:end].decode("utf-8", errors="replace")


def _letter(bind: int, stype: int) -> str:
    if stype == _STT_FUNC:
        return "t" if bind == _STB_LOCAL else "T"
    if stype == _STT_NOTYPE:
        return "U" if bind == _STB_LOCAL else "?"
    if stype == _STT_OBJECT:
        return "D"
    return "?"


def _ror32(val: int, rot: int) -> int:
    rot &= 31
    if rot == 0:
        return val & 0xFFFFFFFF
    return ((val >> rot) | (val << (32 - rot))) & 0xFFFFFFFF


def _decode_arm_add_ip_imm(word: int, rn_expected: int) -> int | None:
    """Decode `add ip, rN, #const` (A1 encoding, cond=AL)."""
    if (word & 0x0FFF0000) != (0x02800000 | (rn_expected << 16)):
        return None
    if ((word >> 12) & 0xF) != 12:
        return None
    imm8 = word & 0xFF
    rot = ((word >> 8) & 0xF) * 2
    return _ror32(imm8, rot)


def _decode_arm_ldr_pc_ip_imm(word: int) -> int | None:
    """Decode `ldr pc, [ip, #+-imm12](!)` (A1 encoding)."""
    if (word & 0x0D5F0000) != 0x051C0000:
        return None
    if ((word >> 12) & 0xF) != 15:
        return None
    imm12 = word & 0xFFF
    up = (word >> 23) & 1
    return imm12 if up else -imm12


class Elf32Image:
    """Fast, pure-stdlib reader for little-endian ARM ELF32 images."""

    def __init__(self, data: bytes, path: Path | None = None) -> None:
        if len(data) < 52 or data[:4] != _ELF_MAGIC:
            raise ValueError(f"{path or '<bytes>'}: not an ELF file")
        if data[4] != 1 or data[5] != 1:
            raise ValueError(f"{path or '<bytes>'}: expected 32-bit little-endian ELF")
        self.data = data
        self.path = path
        self._sha256: str | None = None

        ehdr = struct.unpack_from("<16sHHIIIIIHHHHHH", data, 0)
        (
            _e_ident,
            self.e_type,
            self.e_machine,
            _e_version,
            self.e_entry,
            self.e_phoff,
            self.e_shoff,
            self.e_flags,
            _e_ehsize,
            self.e_phentsize,
            self.e_phnum,
            self.e_shentsize,
            self.e_shnum,
            self.e_shstrndx,
        ) = ehdr

        self.segments: list[ElfSegment] = []
        if self.e_phoff and self.e_phentsize >= 32:
            for idx in range(self.e_phnum):
                off = self.e_phoff + idx * self.e_phentsize
                if off + 32 > len(data):
                    break
                p_type, p_off, p_va, p_pa, p_filesz, p_memsz, p_flags, _align = (
                    struct.unpack_from("<8I", data, off)
                )
                self.segments.append(
                    ElfSegment(p_type, p_off, p_va, p_pa, p_filesz, p_memsz, p_flags)
                )

        raw_shdrs: list[tuple[int, ...]] = []
        if self.e_shoff and self.e_shentsize >= 40:
            for idx in range(self.e_shnum):
                off = self.e_shoff + idx * self.e_shentsize
                if off + 40 > len(data):
                    break
                raw_shdrs.append(struct.unpack_from("<10I", data, off))

        shstrtab = b""
        if 0 <= self.e_shstrndx < len(raw_shdrs):
            shstr = raw_shdrs[self.e_shstrndx]
            shstrtab = data[shstr[4] : shstr[4] + shstr[5]]

        self.sections: list[ElfSection] = []
        self._sections_by_name: dict[str, ElfSection] = {}
        for idx, sh in enumerate(raw_shdrs):
            sec = ElfSection(
                index=idx,
                name=_read_cstr(shstrtab, sh[0]),
                sh_type=sh[1],
                sh_flags=sh[2],
                sh_addr=sh[3],
                sh_offset=sh[4],
                sh_size=sh[5],
                sh_link=sh[6],
                sh_info=sh[7],
                sh_entsize=sh[9],
            )
            self.sections.append(sec)
            if sec.name and sec.name not in self._sections_by_name:
                self._sections_by_name[sec.name] = sec

    @classmethod
    def from_file(cls, path: Path) -> "Elf32Image":
        path = Path(path)
        return cls(path.read_bytes(), path=path)

    @property
    def sha256(self) -> str:
        if self._sha256 is None:
            self._sha256 = hashlib.sha256(self.data).hexdigest()
        return self._sha256

    def section(self, name: str) -> ElfSection | None:
        return self._sections_by_name.get(name)

    def section_bytes(self, sec: ElfSection) -> bytes:
        if sec.sh_type == 8:  # SHT_NOBITS
            return b""
        return self.data[sec.sh_offset : sec.sh_offset + sec.sh_size]

    def load_segments(self) -> list[ElfSegment]:
        return [s for s in self.segments if s.p_type == _PT_LOAD]

    def base_vaddr(self) -> int:
        loads = self.load_segments()
        return min((s.p_vaddr for s in loads), default=0)

    def va_to_offset(self, va: int) -> int | None:
        va_even = va & ~1 if va & 1 else va
        for seg in self.load_segments():
            if seg.p_vaddr <= va_even < seg.p_vaddr + seg.p_filesz:
                return seg.p_offset + (va_even - seg.p_vaddr)
        for sec in self.sections:
            if sec.sh_type != 8 and sec.sh_size and sec.sh_addr <= va_even < sec.sh_addr + sec.sh_size:
                return sec.sh_offset + (va_even - sec.sh_addr)
        return None

    def offset_to_va(self, offset: int) -> int | None:
        for seg in self.load_segments():
            if seg.p_offset <= offset < seg.p_offset + seg.p_filesz:
                return seg.p_vaddr + (offset - seg.p_offset)
        for sec in self.sections:
            if sec.sh_type != 8 and sec.sh_offset <= offset < sec.sh_offset + sec.sh_size:
                return sec.sh_addr + (offset - sec.sh_offset)
        return None

    def executable_ranges(self) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        for sec in self.sections:
            if (sec.sh_flags & _SHF_EXECINSTR) and (sec.sh_flags & _SHF_ALLOC) and sec.sh_size:
                ranges.append((sec.sh_addr, sec.sh_addr + sec.sh_size))
        return ranges

    def largest_code_section(self) -> tuple[int, bytes]:
        best: ElfSection | None = None
        for sec in self.sections:
            if sec.sh_type == 1 and (sec.sh_flags & _SHF_EXECINSTR):
                if best is None or sec.sh_size > best.sh_size:
                    best = sec
        if best is None:
            raise ValueError(f"{self.path or '<bytes>'}: no executable section")
        return best.sh_addr, self.section_bytes(best)

    def _read_symtab_entries(self, sec_name: str, source: str) -> list[ElfSymbol]:
        sec = self.section(sec_name)
        if sec is None or sec.sh_size == 0:
            return []
        if not (0 <= sec.sh_link < len(self.sections)):
            return []
        strtab = self.section_bytes(self.sections[sec.sh_link])
        entsize = sec.sh_entsize or 16
        blob = self.section_bytes(sec)
        out: list[ElfSymbol] = []
        for pos in range(0, len(blob) - 15, entsize):
            st_name, st_value, st_size, st_info, _st_other, _st_shndx = struct.unpack_from(
                "<IIIBBH", blob, pos
            )
            name = _read_cstr(strtab, st_name)
            out.append(
                ElfSymbol(
                    name=name,
                    address=st_value,
                    size=st_size,
                    type=_letter(st_info >> 4, st_info & 0xF),
                    source=source,
                )
            )
        return out

    def symbols(self) -> list[ElfSymbol]:
        """All named static and dynamic symbols, preferring dynsym on duplicates."""
        found: dict[tuple[str, int], ElfSymbol] = {}
        for sec_name, source in ((".dynsym", "dynsym"), (".symtab", "symtab")):
            for sym in self._read_symtab_entries(sec_name, source):
                if not sym.name:
                    continue
                key = (sym.name, sym.address)
                existing = found.get(key)
                if existing is None or (existing.source == "symtab" and source == "dynsym"):
                    found[key] = sym
        return sorted(found.values(), key=lambda s: (s.address, s.name))

    def functions(self) -> list[ElfSymbol]:
        return [s for s in self.symbols() if s.type in ("T", "t")]

    def relocations(self, section_names: tuple[str, ...] = (".rel.plt", ".rel.dyn")) -> list[ElfReloc]:
        out: list[ElfReloc] = []
        sym_cache: dict[int, list[ElfSymbol]] = {}
        for sec_name in section_names:
            sec = self.section(sec_name)
            if sec is None or sec.sh_type not in (_SHT_REL, _SHT_RELA) or sec.sh_size == 0:
                continue
            if sec.sh_link not in sym_cache and 0 <= sec.sh_link < len(self.sections):
                link_sec = self.sections[sec.sh_link]
                sym_cache[sec.sh_link] = self._read_symtab_entries(link_sec.name, link_sec.name.lstrip("."))
            syms = sym_cache.get(sec.sh_link, [])
            entsize = sec.sh_entsize or (8 if sec.sh_type == _SHT_REL else 12)
            blob = self.section_bytes(sec)
            for pos in range(0, len(blob) - 7, entsize):
                r_offset, r_info = struct.unpack_from("<II", blob, pos)
                sym_idx = r_info >> 8
                r_type = r_info & 0xFF
                sym_name = syms[sym_idx].name if 0 <= sym_idx < len(syms) else ""
                sym_val = syms[sym_idx].address if 0 <= sym_idx < len(syms) else 0
                out.append(ElfReloc(sec_name, r_offset, r_info, r_type, sym_idx, sym_name, sym_val))
        return out

    def plt_map(self) -> dict[int, str]:
        """Map PLT stub start VA -> imported symbol name."""
        plt = self.section(".plt")
        if plt is None or plt.sh_size < 32:
            return {}
        rel_plt = self.relocations((".rel.plt",))
        if not rel_plt:
            return {}
        got_to_name = {rel.r_offset: rel.symbol_name for rel in rel_plt if rel.symbol_name}
        blob = self.section_bytes(plt)
        base = plt.sh_addr
        mapping: dict[int, str] = {}

        # Primary decoder: parse ARM PLT sequences (`add ip, pc, #imm; add ip, ip, #imm; ldr pc, [ip, #imm]!`),
        # accepting an optional leading 4-byte Thumb veneer (`bx pc; nop` = 78 47 c0 46).
        pos = 20
        while pos + 12 <= len(blob):
            stub_va = base + pos
            arm_off = pos
            if blob[pos : pos + 4] == b"\x78\x47\xc0\x46":
                arm_off = pos + 4
            if arm_off + 12 > len(blob):
                break
            w0, w1, w2 = struct.unpack_from("<3I", blob, arm_off)
            imm0 = _decode_arm_add_ip_imm(w0, 15)
            imm1 = _decode_arm_add_ip_imm(w1, 12)
            imm2 = _decode_arm_ldr_pc_ip_imm(w2)
            if imm0 is not None and imm1 is not None and imm2 is not None:
                pc_val = base + arm_off + 8
                got_va = (pc_val + imm0 + imm1 + imm2) & 0xFFFFFFFF
                name = got_to_name.get(got_va)
                if name:
                    mapping[stub_va] = name
                pos = arm_off + 12
                continue
            pos += 4

        if mapping:
            return mapping

        # Fallback: standard 12-byte ARM PLT stride matching .rel.plt order.
        for idx, rel in enumerate(rel_plt):
            off = 20 + idx * 12
            if off + 12 <= len(blob) and rel.symbol_name:
                mapping[base + off] = rel.symbol_name
        return mapping

    def rodata_strings(self, min_len: int = 4) -> list[tuple[int, str]]:
        """Extract printable ASCII strings >= ``min_len`` from .rodata sections."""
        out: list[tuple[int, str]] = []
        for sec in self.sections:
            if not sec.name.startswith(".rodata") or sec.sh_type == 8 or not sec.sh_size:
                continue
            blob = self.section_bytes(sec)
            for match in _ASCII_RUN_RE.finditer(blob):
                if len(match.group(0)) >= min_len:
                    out.append((sec.sh_addr + match.start(), match.group(0).decode("ascii")))
        return out


# ---------------------------------------------------------------------------
# Pure-Python Thumb / Thumb-2 decoder & masked-signature engine
# ---------------------------------------------------------------------------

def is_thumb32(hw: int) -> bool:
    return (hw >> 11) in (0b11101, 0b11110, 0b11111)


def thumb_bl_target(data: bytes, pos: int, base: int) -> int | None:
    """Decode 32-bit Thumb ``bl``/``blx`` target in symbol space (LSB=1 for bl)."""
    if (pos & 1) or pos + 4 > len(data):
        return None
    first, second = struct.unpack_from("<HH", data, pos)
    if (first & 0xF800) != 0xF000 or (second & 0xC000) != 0xC000:
        return None
    is_bl = bool(second & 0x1000)
    if not is_bl and (second & 1):
        return None
    sign = (first >> 10) & 1
    j1 = (second >> 13) & 1
    j2 = (second >> 11) & 1
    imm10 = first & 0x3FF
    imm11 = second & 0x7FF
    i1 = 1 - (j1 ^ sign)
    i2 = 1 - (j2 ^ sign)
    offset = (sign << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1)
    if sign:
        offset -= 1 << 25
    pc = base + pos + 4
    if is_bl:
        return ((pc + offset) & 0xFFFFFFFF) | 1
    return ((pc & ~3) + offset) & 0xFFFFFFFF


def direct_calls(
    data: bytes, base: int, code_ranges: list[tuple[int, int]] | None = None
) -> list[tuple[int, int]]:
    """Return ``(caller_va, callee_va_with_thumb_bit)`` direct Thumb calls."""
    edges: list[tuple[int, int]] = []
    pos = 0
    limit = len(data) - 1
    while pos < limit:
        first = int.from_bytes(data[pos : pos + 2], "little")
        if not is_thumb32(first):
            pos += 2
            continue
        target = thumb_bl_target(data, pos, base)
        if target is not None:
            inside = code_ranges is None or any(
                lo <= (target & ~1) < hi for lo, hi in code_ranges
            )
            if inside:
                edges.append((base + pos, target))
        pos += 4
    return edges


def make_enclosing(funcs: list[ElfSymbol]):
    """Map an instruction VA to its enclosing function symbol or ``sub_XXXXXXXX``."""
    spans = sorted(((s.address & ~1), s.size, s.name) for s in funcs if s.address and s.size)
    starts = [start for start, _, _ in spans]

    def enclosing(address: int) -> str:
        even = address & ~1
        index = bisect.bisect_right(starts, even)
        if index:
            start, size, name = spans[index - 1]
            if start <= even < start + size:
                return name
        return f"sub_{even:08X}"

    return enclosing


def build_callgraph_lite(img: Elf32Image) -> dict[str, object]:
    """Direct call graph over ``img`` using pure-Python stdlib decoding."""
    funcs = img.functions()
    names = {sym.address: sym.name for sym in funcs if sym.address}
    plt_names = img.plt_map()
    code_base, code = img.largest_code_section()
    enclosing = make_enclosing(funcs)
    pairs = direct_calls(code, code_base, img.executable_ranges())
    edge_rows: list[tuple[int, str, int, str, str]] = []
    unresolved = 0
    for caller_va, callee_va in pairs:
        caller_name = enclosing(caller_va)
        even_target = callee_va & ~1
        if even_target in plt_names:
            callee_name = plt_names[even_target]
            kind = "plt"
        elif callee_va in names:
            callee_name = names[callee_va]
            kind = "local"
        elif even_target in names:
            callee_name = names[even_target]
            kind = "local"
        else:
            callee_name = f"sub_{even_target:08X}"
            kind = "unresolved"
            unresolved += 1
        edge_rows.append((caller_va, caller_name, callee_va, callee_name, kind))
    return {
        "functions": len(funcs),
        "plt_stubs": len(plt_names),
        "edges": edge_rows,
        "unresolved": unresolved,
    }


def _thumb2_mask_32(hw1: int, hw2: int, *, structural: bool) -> tuple[int, int]:
    """Return ``(m1, m2)`` bitmasks (1=match, 0=wildcard) for one 32-bit Thumb-2 insn."""
    # BL / BLX(immediate)
    if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xC000) == 0xC000:
        return 0xF800, 0xD000  # preserve branch class + bl/blx bit, wildcard offset
    # B.W unconditional
    if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0x9000:
        return 0xF800, 0xD000
    # B<cc>.W conditional
    if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0x8000 and (hw1 & 0x0380) != 0x0380:
        return 0xFBC0, 0xD000  # preserve cond, wildcard displacement
    # MOVW / MOVT (T3/T1) -> keep opcode & Rd, wildcard imm16
    if (hw1 & 0xFB50) == 0xF240 and (hw2 & 0x8000) == 0:
        if (hw1 & 0x00B0) in (0x0040, 0x00C0):
            return 0xFBF0, 0x0F00
    # LDR (literal) PC-relative: 1111 100U 0101 1111 | Rt imm12
    if (hw1 & 0xFF7F) == 0xF85F:
        return 0xFF7F, 0xF000
    # ADR.W (T2/T3): 1111 0 i 10 x 010 1111 | 0 imm3 Rd imm8
    if (hw1 & 0xFBEF) == 0xF2AF or (hw1 & 0xFBEF) == 0xF20F:
        if (hw2 & 0x8000) == 0:
            return 0xFBEF, 0x0F00

    if not structural:
        return 0xFFFF, 0xFFFF

    # Structural relaxation for cross-version signatures:
    # ADD.W / SUB.W Rd, SP, #const (frame pointer setup, e.g. add.w r7, sp, #imm)
    if (hw1 & 0xFBEF) == 0xF10D or (hw1 & 0xFBEF) == 0xF1AD:
        return 0xFBFF, 0x0F00
    # T2 modified immediate data-processing: mov.w / add.w / sub.w imm12
    if (hw1 & 0xFA00) == 0xF000 and (hw2 & 0x8000) == 0:
        return 0xFBE0, 0x0F00
    # LDR/STR.W Rt, [Rn, #imm12] (struct field offsets drift across builds)
    if (hw1 & 0xFE00) == 0xF800 and (hw1 & 0x0080):
        # keep size/load/Rn, mask Rt (unless sp/pc) & imm12
        return 0xFE1F, 0x0000
    # PUSH.W / POP.W -> keep opcode & lr/pc bits, allow minor callee-saved reg diffs
    if (hw1 == 0xE92D) or (hw1 == 0xE8BD):
        return 0xFFFF, 0xC000
    return 0xFFFF, 0xFFFF


def _thumb16_mask(hw: int, *, structural: bool) -> int:
    """Return 16-bit mask for one 16-bit Thumb instruction."""
    # LDR Rt, [PC, #imm8]
    if (hw & 0xF800) == 0x4800:
        return 0xFF00
    # ADR Rd, #imm8
    if (hw & 0xF800) == 0xA000:
        return 0xFF00
    # B<cc> / B short
    if (hw & 0xF000) == 0xD000 and (hw & 0x0F00) < 0x0E00:
        return 0xFF00
    if (hw & 0xF800) == 0xE000:
        return 0xF800
    # CBZ / CBNZ
    if (hw & 0xF500) == 0xB100:
        return 0xFD07

    if not structural:
        return 0xFFFF

    # Structural: frame/offset immediates & small reg allocation shifts
    # ADD/SUB SP, #imm7 (e.g. sub sp, #imm)
    if (hw & 0xFF00) == 0xB000:
        return 0xFF80
    # ADD Rd, SP, #imm8 (e.g. add r7, sp, #imm)
    if (hw & 0xF800) == 0xA800:
        return 0xFF00
    # LDR/STR Rt, [Rn, #imm5] (struct field offsets & temp regs)
    if (hw & 0xE000) == 0x6000:
        return 0xF800
    # MOV reg, reg (high/low) -> keep opcode, allow temp reg shifts
    if (hw & 0xFF00) == 0x4600:
        return 0xFF00
    return 0xFFFF


def extract_masked_thumb2(
    data: bytes, offset: int, insn_count: int = 16, *, structural: bool = False
) -> tuple[bytes, bytes]:
    """Extract ``(pattern, mask)`` bytes covering ``insn_count`` Thumb/Thumb-2 insns."""
    pat = bytearray()
    msk = bytearray()
    pos = offset & ~1
    taken = 0
    while taken < insn_count and pos + 2 <= len(data):
        hw1 = struct.unpack_from("<H", data, pos)[0]
        if is_thumb32(hw1) and pos + 4 <= len(data):
            hw2 = struct.unpack_from("<H", data, pos + 2)[0]
            m1, m2 = _thumb2_mask_32(hw1, hw2, structural=structural)
            pat.extend(struct.pack("<HH", hw1 & m1, hw2 & m2))
            msk.extend(struct.pack("<HH", m1, m2))
            pos += 4
        else:
            m1 = _thumb16_mask(hw1, structural=structural)
            pat.extend(struct.pack("<H", hw1 & m1))
            msk.extend(struct.pack("<H", m1))
            pos += 2
        taken += 1
    if taken == 0:
        raise ValueError(f"Offset 0x{offset:x} out of bounds for Thumb-2 extraction")
    return bytes(pat), bytes(msk)


def scan_masked(
    haystack: bytes, pattern: bytes, mask: bytes, *, step: int = 2
) -> list[int]:
    """Return every byte offset in ``haystack`` matching ``(byte & mask[i]) == (pat[i] & mask[i])``."""
    n = len(pattern)
    if n == 0 or len(mask) != n or len(haystack) < n:
        return []

    norm = bytes(p & m for p, m in zip(pattern, mask))

    # Find longest contiguous unmasked (0xFF) run for fast C-level bytes.find() prefilter.
    best_start = -1
    best_len = 0
    cur_start = -1
    for idx, m in enumerate(mask):
        if m == 0xFF:
            if cur_start < 0:
                cur_start = idx
        else:
            if cur_start >= 0 and idx - cur_start > best_len:
                best_start, best_len = cur_start, idx - cur_start
            cur_start = -1
    if cur_start >= 0 and n - cur_start > best_len:
        best_start, best_len = cur_start, n - cur_start

    pairs = [(i, norm[i], mask[i]) for i in range(n) if mask[i] != 0 and not (best_start <= i < best_start + best_len)]
    hits: list[int] = []

    if best_len >= 2:
        anchor = norm[best_start : best_start + best_len]
        search_from = 0
        limit = len(haystack) - n
        while True:
            found = haystack.find(anchor, search_from)
            if found < 0:
                break
            off = found - best_start
            search_from = found + 1
            if off < 0 or off > limit or (step > 1 and (off % step) != 0):
                continue
            if all((haystack[off + i] & m) == exp for i, exp, m in pairs):
                hits.append(off)
    else:
        for off in range(0, len(haystack) - n + 1, step):
            if all((haystack[off + i] & m) == exp for i, exp, m in pairs):
                hits.append(off)
    return hits


def _constant_r0_before_call(code: bytes, site_off: int, window: int = 0x40) -> int | None:
    """Decode straight-line constant written into ``r0`` immediately before a call."""
    start = max(0, site_off - window)
    # Try 2-byte aligned starts and require exact landing on site_off.
    for cand_start in range(start, site_off, 2):
        pos = cand_start
        insns: list[tuple[int, int, int | None]] = []
        while pos < site_off:
            hw1 = struct.unpack_from("<H", code, pos)[0]
            if is_thumb32(hw1) and pos + 4 <= site_off:
                hw2 = struct.unpack_from("<H", code, pos + 2)[0]
                insns.append((4, hw1, hw2))
                pos += 4
            elif not is_thumb32(hw1) and pos + 2 <= site_off:
                insns.append((2, hw1, None))
                pos += 2
            else:
                insns = []
                break
        if pos != site_off or not insns:
            continue
        r0_val: int | None = None
        for size, hw1, hw2 in insns:
            if size == 2:
                if (hw1 & 0xF800) == 0x2000:  # movs r0, #imm8 (rd == 0)
                    r0_val = hw1 & 0xFF
                elif (hw1 & 0xFE00) in (0x1C00, 0x1E00, 0x3000) and (hw1 & 0x7) == 0:
                    r0_val = None
            else:
                assert hw2 is not None
                # bl / blx clobbers r0
                if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xC000) == 0xC000:
                    r0_val = None
                # movw / movt r0, #imm16 (Rd == 0)
                elif (hw1 & 0xFB50) == 0xF240 and (hw2 & 0x8F00) == 0x0000:
                    imm4 = hw1 & 0xF
                    i_bit = (hw1 >> 10) & 1
                    imm3 = (hw2 >> 12) & 0x7
                    imm8 = hw2 & 0xFF
                    imm16 = (imm4 << 12) | (i_bit << 11) | (imm3 << 8) | imm8
                    if (hw1 & 0x0080) == 0:  # movw
                        r0_val = imm16
                    else:  # movt
                        r0_val = ((r0_val or 0) & 0xFFFF) | (imm16 << 16)
        return r0_val
    return None


def extract_ipc_sites_lite(
    img: Elf32Image,
    callees: tuple[str, ...] = ("osal_snd_msg", "osal_rcv_msg_tmo", "osal_snd_sync_msg", "ioctl"),
) -> list[dict[str, object]]:
    """Direct IPC call sites with statically resolved constant ``r0`` queue/cmd IDs."""
    target_set = set(callees)
    plt_names = img.plt_map()
    funcs = img.functions()
    names = {sym.address: sym.name for sym in funcs if sym.address}
    enclosing = make_enclosing(funcs)
    code_base, code = img.largest_code_section()
    pairs = direct_calls(code, code_base, img.executable_ranges())
    rows: list[dict[str, object]] = []
    for site_va, callee_va in pairs:
        even = callee_va & ~1
        callee_name = plt_names.get(even) or names.get(callee_va) or names.get(even)
        if callee_name not in target_set:
            continue
        const_r0 = _constant_r0_before_call(code, site_va - code_base)
        rows.append(
            {
                "site": site_va,
                "caller": enclosing(site_va),
                "callee": callee_name,
                "queue_or_cmd": const_r0,
            }
        )
    return rows
