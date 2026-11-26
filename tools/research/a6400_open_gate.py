"""Offline planner and Unicorn verifier for squeezed 3:2 Open Gate on ILCE-6400.

Ports the A7 III anamorphic/squeezed Open Gate design (``og_m3_v2`` / ``m3og_v3``)
to live ILCE-6400 ``av-cam.bin`` (``dd8f8d0cc74ad4b7...``, ORIL VA base ``0x635C6000``).

Strictly offline and non-destructive: builds in-memory patch sets, checks all
stock baseline bytes against the reference ``av-cam.bin`` image, and verifies
the patched tables inside Unicorn via ``0x6379d618`` (reglist applicator),
``0x63771d78``/``0x63771de4`` (FE record selectors), and ``0x63cf0018``
(``rt_t14`` timing table populator). Physical camera writes are not executed.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from project_paths import PROJECT_ROOT

AVCAM_ORIL_BASE = 0x635C6000
AVCAM_PHYS_BASE = 0x035C6000
AVCAM_PHYS_DELTA = 0x60000000
AVCAM_SIZE = 16_803_048
AVCAM_SHA256 = "dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209"
DEFAULT_AVCAM_PATH = PROJECT_ROOT / "artifacts" / "hardware" / "av-cam-camera-original.bin"

# IMX284 tables & code VAs in dd8f8d0c
DRIVE_KEYS_VA = 0x63E25514
MODE_TO_COL_VA = 0x63F109E1
REGLIST_VA = 0x6454C484
REGLIST_ROWS = 125
REGLIST_ROW_STRIDE = 20
REGLIST_COL_4K = 11

REGLIST_APPLY_FN = 0x6379D618
FE_SELECTOR_MAIN_FN = 0x63771D78
FE_SELECTOR_AUX_FN = 0x63771DE4
RT_T14_POPULATE_FN = 0x63CF0018
RT_T10_POPULATE_FN = 0x63CEFF4E

FE_REC_0A_VA = 0x63E14E94
FE_REC_19_VA = 0x63E15E54
FE_REC_SIZE = 1344
FE_BASE_HEIGHT = 3392
FE_SCALE_BASE_20E = 5818

TIMING_NTSC_VA = 0x63E2580C
TIMING_PAL_VA = 0x63E259D4
TIMING_ROM_STRIDE = 24

RT_T10_VA = 0x64200EFC
RT_T10_STRIDE = 0x30
RT_T10_DERIVED_OFF = 0x24

RT_T14_VA = 0x6420128C
RT_T14_STRIDE = 0x78
RT_T14_H1_OFF = 0x08
RT_T14_V1_OFF = 0x0C
RT_T14_VACTIVE_OFF = 0x50
RT_T14_H2_OFF = 0x68
RT_T14_V2_OFF = 0x6C

SIZE_ROWS_M16_BASE_VA = 0x63E25430
SIZE_ROWS_M17_BASE_VA = 0x63E2547C
SIZE_ROWS_VACTIVE_OFF = 0x3C
SIZE_ROWS_M16_VACTIVE_VA = SIZE_ROWS_M16_BASE_VA + SIZE_ROWS_VACTIVE_OFF  # 0x63E2546C
SIZE_ROWS_M17_VACTIVE_VA = SIZE_ROWS_M17_BASE_VA + SIZE_ROWS_VACTIVE_OFF  # 0x63E254B8
STOCK_V_ACTIVE = 3376

# Receiver frame descriptor vertical fields for 4K 24p/25p (6000x3376 @ 0x63E19740 / 0x63E19780)
RCV_DESC_FIELDS: Tuple[Tuple[int, int, str], ...] = (
    (0x63E19754, 3396, "rcv_a_ysize"),
    (0x63E19768, 3392, "rcv_a_fe_h"),
    (0x63E19794, 3396, "rcv_b_ysize"),
    (0x63E19798, 3376, "rcv_b_vact"),
    (0x63E197AA, 3392, "rcv_b_fe_h"),
    (0x63E197B0, 3447, "rcv_b_vtot"),
)

MODE_24P_INDEX = 16
MODE_25P_INDEX = 17
MODE_DRIVE_KEYS: Mapping[int, int] = {
    MODE_24P_INDEX: 0x1F,
    MODE_25P_INDEX: 0x89,
}

STOCK_Y_START = 342
STOCK_Y_SIZE = 3396
STOCK_Y_CENTER_SUM = STOCK_Y_START * 2 + STOCK_Y_SIZE  # 4080 -> Y_START + Y_SIZE/2 == 2040

# (row_index, sensor_register, staging_value_byte_va, field_name)
# The staging record is [uint16 sensor_addr][uint16 value]. The reglist destination
# points at the value low byte. The address halfword is at pointer-2. The halfword
# immediately after the value byte is the next record's address (0x0025, 0x001E,
# 0x0027, 0x001B), which earlier notes treated as the register id.
REGLIST_COL11_Y_ROWS: Tuple[Tuple[int, int, int, str], ...] = (
    (10, 0x000A, 0x645A1FE8, "Y_START_LO"),
    (11, 0x000B, 0x645A1ED0, "Y_START_HI"),
    (12, 0x000C, 0x645A1DAC, "Y_SIZE_LO"),
    (13, 0x000D, 0x645A1F24, "Y_SIZE_HI"),
)

# Vertical uint16 offsets inside fe_rec_0a / fe_rec_19 (65 from A7 III og_m3_v2 + 0x0FA on ILCE-6400)
FE_OFFSETS_PLUS_DY: Tuple[int, ...] = (
    0x002, 0x00A, 0x012, 0x016, 0x01E, 0x026, 0x02A, 0x032, 0x03A, 0x03E,
    0x046, 0x04E, 0x082, 0x08A, 0x092, 0x0AA, 0x0B2, 0x0BA, 0x0BE, 0x0C6,
    0x0CE, 0x0FA, 0x108, 0x114, 0x11C, 0x144, 0x14C, 0x154, 0x15C, 0x160,
    0x190, 0x198, 0x1A0, 0x1A8, 0x1B0, 0x1B8, 0x1BC, 0x1C0, 0x1C4, 0x1CC,
    0x1D4, 0x202, 0x206, 0x218, 0x220, 0x228, 0x22C, 0x230, 0x242, 0x3B8,
    0x3C0, 0x3C8, 0x3CC, 0x3E6, 0x3F4, 0x3FC, 0x404, 0x408, 0x40C, 0x426,
    0x432,
)
FE_OFFSETS_PLUS_HALF_DY: Tuple[int, ...] = (0x1E4, 0x1EC)
FE_OFFSETS_PLUS_QUARTER_DY: Tuple[int, ...] = (0x250, 0x254)
FE_OFFSET_SCALED = 0x20E
FE_ALL_OFFSETS: Tuple[int, ...] = (
    FE_OFFSETS_PLUS_DY + FE_OFFSETS_PLUS_HALF_DY + FE_OFFSETS_PLUS_QUARTER_DY + (FE_OFFSET_SCALED,)
)

STOCK_TIMING_HV: Mapping[int, Tuple[int, int]] = {
    MODE_24P_INDEX: (862, 1740),  # h*v = 1,499,880
    MODE_25P_INDEX: (827, 1740),  # h*v = 1,438,980
}


@dataclass(frozen=True)
class StepSpec:
    """Configuration for a graduated Open Gate test stage."""

    name: str
    dy: int
    patch_sensor: bool
    patch_fe: bool
    patch_timing: bool
    description: str


STEPS: Mapping[str, StepSpec] = {
    "putback": StepSpec("putback", 0, True, True, True, "Restore stock 3396-row 16:9 4K parameters"),
    "test1": StepSpec("test1", 624, True, False, False, "Sensor-only +624 rows (FE & timing stock)"),
    "test4": StepSpec("test4", 160, True, True, True, "+160 rows (~4.7% squeeze, Bayer-aligned) with FE & lockstep timing"),
    "test3": StepSpec("test3", 312, True, True, True, "+312 rows (~9% squeeze) with FE & lockstep timing"),
    "test2": StepSpec("test2", 624, True, True, True, "+624 rows (full 3:2 Open Gate) with FE & lockstep timing"),
    "shrink": StepSpec("shrink", -160, True, True, False, "Diagnostic -160 rows (stock timing) testing FE/SGC scaling"),
    "plus24": StepSpec("plus24", 24, True, True, False, "Diagnostic +24 rows (stock timing, < 3480 VMAX wall)"),
    "plus8": StepSpec("plus8", 8, True, True, False, "Diagnostic +8 rows (stock timing, minimal upward bisect)"),
}


@dataclass(frozen=True)
class PatchWrite:
    """Single contiguous byte patch at an av-cam.bin virtual address."""

    block: str
    va: int
    expected: bytes
    patched: bytes
    note: str = ""

    @property
    def file_offset(self) -> int:
        return self.va - AVCAM_ORIL_BASE

    @property
    def phys_addr(self) -> int:
        return self.va - AVCAM_PHYS_DELTA


def compute_sensor_geometry(dy: int) -> Tuple[int, int]:
    """Return centered, Bayer-aligned ``(Y_START, Y_SIZE)`` for ``dy`` vertical line delta.

    ``dy`` must be divisible by 8 so ``Y_START`` stays congruent to 2 mod 4 (matching every
    stock IMX284 ``reglist`` column and preventing a Bayer row-parity color flip).
    """
    if dy % 8 != 0:
        raise ValueError(f"dy must be divisible by 8 (Bayer parity), got {dy}")
    y_size = STOCK_Y_SIZE + dy
    y_start = STOCK_Y_START - (dy // 2)
    if y_start < 0 or y_size <= 0:
        raise ValueError(f"Invalid geometry for dy={dy}: Y_START={y_start}, Y_SIZE={y_size}")
    return y_start, y_size


def compute_timing_hv(mode_index: int, dy: int) -> Tuple[int, int]:
    """Compute lockstep ``(h, v)`` preserving ``h * v`` as sensor rows increase by ``dy``."""
    h0, v0 = STOCK_TIMING_HV[mode_index]
    if dy == 0:
        return h0, v0
    v_new = v0 + (dy // 2)
    h_new = round((h0 * v0) / v_new)
    return h_new, v_new


def compute_fe_field_value(offset: int, stock_value: int, dy: int) -> int:
    """Apply the A7 III ``og_m3_v2`` vertical scaling rule to one FE uint16 offset."""
    if dy == 0:
        return stock_value
    if offset in FE_OFFSETS_PLUS_DY:
        return stock_value + dy
    if offset in FE_OFFSETS_PLUS_HALF_DY:
        return stock_value + (dy // 2)
    if offset in FE_OFFSETS_PLUS_QUARTER_DY:
        return stock_value + (dy // 4)
    if offset == FE_OFFSET_SCALED:
        return round(FE_SCALE_BASE_20E * (FE_BASE_HEIGHT + dy) / FE_BASE_HEIGHT)
    raise KeyError(f"Unsupported FE offset {offset:#x}")


def _reglist_row_col11_va(row_idx: int) -> int:
    return REGLIST_VA + row_idx * REGLIST_ROW_STRIDE + 4 + REGLIST_COL_4K


def _timing_rom_row_va(table_va: int, mode_index: int) -> int:
    return table_va + mode_index * TIMING_ROM_STRIDE


def build_patch_plan(image: bytes, step_name: str, verify_stock: bool = True) -> List[PatchWrite]:
    """Build deterministic byte-level patches for ``step_name`` from stock ``av-cam.bin``."""
    spec = STEPS[step_name]
    patches: List[PatchWrite] = []

    if spec.patch_sensor:
        y_start, y_size = compute_sensor_geometry(spec.dy)
        new_bytes = {
            0x000A: y_start & 0xFF,
            0x000B: (y_start >> 8) & 0xFF,
            0x000C: y_size & 0xFF,
            0x000D: (y_size >> 8) & 0xFF,
        }
        old_bytes = {
            0x000A: STOCK_Y_START & 0xFF,
            0x000B: (STOCK_Y_START >> 8) & 0xFF,
            0x000C: STOCK_Y_SIZE & 0xFF,
            0x000D: (STOCK_Y_SIZE >> 8) & 0xFF,
        }
        for row_idx, reg_id, _ptr, name in REGLIST_COL11_Y_ROWS:
            va = _reglist_row_col11_va(row_idx)
            exp = bytes([old_bytes[reg_id]])
            if verify_stock:
                actual = image[va - AVCAM_ORIL_BASE : va - AVCAM_ORIL_BASE + 1]
                if actual != exp:
                    raise ValueError(f"Stock mismatch at {name} ({va:#x}): got {actual.hex()}, expected {exp.hex()}")
            patches.append(PatchWrite("reglist", va, exp, bytes([new_bytes[reg_id]]), f"row {row_idx} reg {reg_id:#06x} {name}"))

    fe_dy = spec.dy if spec.patch_fe else 0
    for block_name, rec_va in (("fe_rec_0a", FE_REC_0A_VA), ("fe_rec_19", FE_REC_19_VA)):
        base_off = rec_va - AVCAM_ORIL_BASE
        for off in FE_ALL_OFFSETS:
            stock_val = struct.unpack_from("<H", image, base_off + off)[0]
            new_val = compute_fe_field_value(off, stock_val, fe_dy)
            if verify_stock and off == FE_OFFSET_SCALED and stock_val != FE_SCALE_BASE_20E:
                raise ValueError(f"Unexpected FE +0x20e stock value {stock_val} at {rec_va + off:#x}")
            patches.append(
                PatchWrite(
                    block_name,
                    rec_va + off,
                    struct.pack("<H", stock_val),
                    struct.pack("<H", new_val),
                    f"+{off:#05x}: {stock_val}->{new_val}",
                )
            )

    timing_dy = spec.dy if spec.patch_timing else 0
    for mode_idx in (MODE_24P_INDEX, MODE_25P_INDEX):
        h0, v0 = STOCK_TIMING_HV[mode_idx]
        h_new, v_new = compute_timing_hv(mode_idx, timing_dy)
        exp_pair = struct.pack("<II", h0, v0)
        new_pair = struct.pack("<II", h_new, v_new)

        for tv_name, table_va in (("timing_rom_ntsc", TIMING_NTSC_VA), ("timing_rom_pal", TIMING_PAL_VA)):
            row_va = _timing_rom_row_va(table_va, mode_idx)
            for slot_off, slot_label in ((0x00, "hv1"), (0x0C, "hv2")):
                va = row_va + slot_off
                if verify_stock:
                    actual = image[va - AVCAM_ORIL_BASE : va - AVCAM_ORIL_BASE + 8]
                    if actual != exp_pair:
                        raise ValueError(f"Stock mismatch at {tv_name}[{mode_idx}] {slot_label} ({va:#x})")
                patches.append(
                    PatchWrite(
                        tv_name,
                        va,
                        exp_pair,
                        new_pair,
                        f"mode {mode_idx} {slot_label} ({h0},{v0})->({h_new},{v_new})",
                    )
                )

    exp_vact = struct.pack("<I", STOCK_V_ACTIVE)
    new_vact = struct.pack("<I", STOCK_V_ACTIVE + fe_dy)
    for mode_idx, vact_va in ((MODE_24P_INDEX, SIZE_ROWS_M16_VACTIVE_VA), (MODE_25P_INDEX, SIZE_ROWS_M17_VACTIVE_VA)):
        if verify_stock:
            actual = image[vact_va - AVCAM_ORIL_BASE : vact_va - AVCAM_ORIL_BASE + 4]
            if actual != exp_vact:
                raise ValueError(f"Stock V_ACTIVE mismatch at size_rows[{mode_idx}]+0x3c ({vact_va:#x})")
        patches.append(
            PatchWrite(
                "size_rows_vactive",
                vact_va,
                exp_vact,
                new_vact,
                f"mode {mode_idx} V_ACTIVE {STOCK_V_ACTIVE}->{STOCK_V_ACTIVE + fe_dy}",
            )
        )

    for rcv_va, rcv_stock, rcv_label in RCV_DESC_FIELDS:
        exp_u16 = struct.pack("<H", rcv_stock)
        new_u16 = struct.pack("<H", rcv_stock + fe_dy)
        if verify_stock:
            actual = image[rcv_va - AVCAM_ORIL_BASE : rcv_va - AVCAM_ORIL_BASE + 2]
            if actual != exp_u16:
                raise ValueError(f"Stock {rcv_label} mismatch at {rcv_va:#x}: got {actual.hex()}, expected {exp_u16.hex()}")
        patches.append(
            PatchWrite(
                "rcv_desc",
                rcv_va,
                exp_u16,
                new_u16,
                f"{rcv_label} {rcv_stock}->{rcv_stock + fe_dy}",
            )
        )

    return patches


def build_runtime_timing_targets(step_name: str) -> List[PatchWrite]:
    """Build runtime ``rt_t14`` targets (``0x6420128c`` .bss, populated at boot by ``0x6379d500``)."""
    spec = STEPS[step_name]
    timing_dy = spec.dy if spec.patch_timing else 0
    fe_dy = spec.dy if spec.patch_fe else 0
    targets: List[PatchWrite] = []
    for mode_idx in (MODE_24P_INDEX, MODE_25P_INDEX):
        h0, v0 = STOCK_TIMING_HV[mode_idx]
        h_new, v_new = compute_timing_hv(mode_idx, timing_dy)
        exp_pair = struct.pack("<II", h0, v0)
        new_pair = struct.pack("<II", h_new, v_new)
        entry_va = RT_T14_VA + mode_idx * RT_T14_STRIDE
        for slot_off, slot_label in ((RT_T14_H1_OFF, "rt_hv1"), (RT_T14_H2_OFF, "rt_hv2")):
            targets.append(
                PatchWrite(
                    "timing_rt_t14",
                    entry_va + slot_off,
                    exp_pair,
                    new_pair,
                    f"mode {mode_idx} {slot_label} ({h0},{v0})->({h_new},{v_new})",
                )
            )
    exp_vact = struct.pack("<I", STOCK_V_ACTIVE)
    new_vact = struct.pack("<I", STOCK_V_ACTIVE + fe_dy)
    for mode_idx in (MODE_24P_INDEX, MODE_25P_INDEX):
        entry_va = RT_T14_VA + mode_idx * RT_T14_STRIDE
        targets.append(
            PatchWrite(
                "rt_t14_vactive",
                entry_va + RT_T14_VACTIVE_OFF,
                exp_vact,
                new_vact,
                f"mode {mode_idx} rt_vactive {STOCK_V_ACTIVE}->{STOCK_V_ACTIVE + fe_dy}",
            )
        )
    return targets


def apply_patches(image: bytearray, patches: Sequence[PatchWrite]) -> None:
    """Apply ``patches`` in-place after checking expected baseline bytes."""
    for p in patches:
        off = p.file_offset
        cur = bytes(image[off : off + len(p.expected)])
        if cur != p.expected:
            raise ValueError(f"Refusing write to {p.block} @ {p.va:#x}: got {cur.hex()}, expected {p.expected.hex()}")
        image[off : off + len(p.patched)] = p.patched


def revert_patches(image: bytearray, patches: Sequence[PatchWrite]) -> None:
    """Restore ``patches`` in-place after checking patched bytes are present."""
    for p in patches:
        off = p.file_offset
        cur = bytes(image[off : off + len(p.patched)])
        if cur != p.patched:
            raise ValueError(f"Refusing revert at {p.block} @ {p.va:#x}: got {cur.hex()}, expected {p.patched.hex()}")
        image[off : off + len(p.expected)] = p.expected


def _emulate_reglist_and_fe_and_t14(image: bytes, step_name: str) -> Dict[str, object]:
    """Run Unicorn over ``0x6379d618``, ``0x63771d78``, ``0x63771de4``, and ``0x63cf0018``."""
    from unicorn import UC_ARCH_ARM, UC_MODE_THUMB, UC_PROT_ALL, Uc
    from unicorn.arm_const import (
        UC_ARM_REG_LR,
        UC_ARM_REG_R0,
        UC_ARM_REG_R1,
        UC_ARM_REG_R2,
        UC_ARM_REG_R3,
        UC_ARM_REG_SP,
    )

    patches = build_patch_plan(image, step_name, verify_stock=True)
    patched_img = bytearray(image)
    apply_patches(patched_img, patches)

    uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)

    # Map av-cam.bin VA range aligned to 4 KiB
    map_start = AVCAM_ORIL_BASE
    map_size = (len(patched_img) + 0xFFF) & ~0xFFF
    uc.mem_map(map_start, map_size, UC_PROT_ALL)
    uc.mem_write(map_start, bytes(patched_img))

    # Map scratch workspace (object + stack + synthetic rom_t30 + staging safety buffer)
    scratch_va = 0x70000000
    uc.mem_map(scratch_va, 0x4000, UC_PROT_ALL)
    stop_sentinel = scratch_va + 0x3F00
    uc.mem_write(stop_sentinel, b"\x00\xbf\x00\xbf")  # nop; nop

    # 1) Emulate 0x6379d618 (mode_idx=16 -> col=11 -> writes reglist entries)
    obj_va = scratch_va + 0x100
    uc.mem_write(obj_va + 0x254, b"\x00")  # skip secondary table scan
    uc.reg_write(UC_ARM_REG_SP, scratch_va + 0x2000)
    uc.reg_write(UC_ARM_REG_LR, stop_sentinel | 1)
    uc.reg_write(UC_ARM_REG_R0, obj_va)
    uc.reg_write(UC_ARM_REG_R1, MODE_24P_INDEX)
    uc.emu_start(REGLIST_APPLY_FN | 1, stop_sentinel, timeout=500_000, count=50_000)

    reg_vals = {
        name: uc.mem_read(ptr, 1)[0]
        for _row, _reg, ptr, name in REGLIST_COL11_Y_ROWS
    }
    y_start = reg_vals["Y_START_LO"] | (reg_vals["Y_START_HI"] << 8)
    y_size = reg_vals["Y_SIZE_LO"] | (reg_vals["Y_SIZE_HI"] << 8)

    # 2) Emulate FE selectors 0x63771d78 and 0x63771de4 for variants 10 (0x0a) and 25 (0x19)
    fe_results: Dict[Tuple[int, int], int] = {}
    fe_heights: Dict[Tuple[int, int], Tuple[int, int, int]] = {}
    for fn_va in (FE_SELECTOR_MAIN_FN, FE_SELECTOR_AUX_FN):
        for variant in (10, 25):
            uc.mem_write(obj_va + 8, struct.pack("<I", variant))
            uc.reg_write(UC_ARM_REG_SP, scratch_va + 0x2000)
            uc.reg_write(UC_ARM_REG_LR, stop_sentinel | 1)
            uc.reg_write(UC_ARM_REG_R0, obj_va)
            uc.emu_start(fn_va | 1, stop_sentinel, timeout=100_000, count=5_000)
            ret_va = uc.reg_read(UC_ARM_REG_R0)
            fe_results[(fn_va, variant)] = ret_va
            v002 = struct.unpack("<H", uc.mem_read(ret_va + 0x002, 2))[0]
            v00a = struct.unpack("<H", uc.mem_read(ret_va + 0x00A, 2))[0]
            v20e = struct.unpack("<H", uc.mem_read(ret_va + 0x20E, 2))[0]
            fe_heights[(fn_va, variant)] = (v002, v00a, v20e)

    # 3) Emulate 0x63cf0018 (rt_t14 populator) on NTSC 24p and 25p size_rows + timing rows
    t14_results: Dict[int, Tuple[int, int, int, int]] = {}
    t14_vactive: Dict[int, int] = {}
    size_rows_map = {
        MODE_24P_INDEX: SIZE_ROWS_M16_BASE_VA,
        MODE_25P_INDEX: SIZE_ROWS_M17_BASE_VA,
    }
    fake_m18 = scratch_va + 0x500
    uc.mem_write(fake_m18, b"\x00")
    for mode_idx in (MODE_24P_INDEX, MODE_25P_INDEX):
        out_t14 = scratch_va + 0x600 + (mode_idx * 0x80)
        timing_row_va = _timing_rom_row_va(TIMING_NTSC_VA, mode_idx)
        sp = scratch_va + 0x2000
        # Before push (3 regs = 12 B), caller [sp+4] = arg4 -> [sp+16], [sp+8] = arg5 -> [sp+20]
        uc.mem_write(sp + 4, struct.pack("<II", 0xCAFEBABE, 0x00001234))
        uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(UC_ARM_REG_LR, stop_sentinel | 1)
        uc.reg_write(UC_ARM_REG_R0, out_t14)
        uc.reg_write(UC_ARM_REG_R1, size_rows_map[mode_idx])
        uc.reg_write(UC_ARM_REG_R2, timing_row_va)
        uc.reg_write(UC_ARM_REG_R3, fake_m18)
        uc.emu_start(RT_T14_POPULATE_FN | 1, stop_sentinel, timeout=100_000, count=5_000)
        h1, v1 = struct.unpack("<II", uc.mem_read(out_t14 + RT_T14_H1_OFF, 8))
        h2, v2 = struct.unpack("<II", uc.mem_read(out_t14 + RT_T14_H2_OFF, 8))
        vact = struct.unpack("<I", uc.mem_read(out_t14 + RT_T14_VACTIVE_OFF, 4))[0]
        t14_results[mode_idx] = (h1, v1, h2, v2)
        t14_vactive[mode_idx] = vact

    # Revert test on in-memory buffer
    revert_patches(patched_img, patches)
    if bytes(patched_img) != image:
        raise AssertionError(f"Putback did not restore byte-for-byte original for step {step_name}")

    return {
        "step": step_name,
        "patch_count": len(patches),
        "y_start": y_start,
        "y_size": y_size,
        "fe_results": fe_results,
        "fe_heights": fe_heights,
        "t14_results": t14_results,
        "t14_vactive": t14_vactive,
    }


def verify_offline(avcam_path: Path = DEFAULT_AVCAM_PATH) -> List[Dict[str, object]]:
    """Run full stock SHA-256, patch baseline, Unicorn execution, and putback checks."""
    image = avcam_path.read_bytes()
    sha256 = hashlib.sha256(image).hexdigest()
    if len(image) != AVCAM_SIZE or sha256 != AVCAM_SHA256:
        raise ValueError(f"Unexpected av-cam.bin: size={len(image)}, sha256={sha256}")

    # Verify drive keys and mode->col[16,17]
    for mode_idx, exp_key in MODE_DRIVE_KEYS.items():
        got_key = struct.unpack_from("<I", image, (DRIVE_KEYS_VA - AVCAM_ORIL_BASE) + mode_idx * 4)[0]
        if got_key != exp_key:
            raise ValueError(f"Drive key[{mode_idx}]={got_key:#x}, expected {exp_key:#x}")
        got_col = image[(MODE_TO_COL_VA - AVCAM_ORIL_BASE) + mode_idx]
        if got_col != REGLIST_COL_4K:
            raise ValueError(f"mode_to_col[{mode_idx}]={got_col}, expected {REGLIST_COL_4K}")

    reports: List[Dict[str, object]] = []
    for step_name in ("putback", "test1", "test4", "test3", "test2", "shrink", "plus24", "plus8"):
        spec = STEPS[step_name]
        rep = _emulate_reglist_and_fe_and_t14(image, step_name)

        exp_y_start, exp_y_size = compute_sensor_geometry(spec.dy if spec.patch_sensor else 0)
        assert (rep["y_start"], rep["y_size"]) == (exp_y_start, exp_y_size)
        assert rep["fe_results"][(FE_SELECTOR_MAIN_FN, 10)] == FE_REC_0A_VA
        assert rep["fe_results"][(FE_SELECTOR_MAIN_FN, 25)] == FE_REC_19_VA
        assert rep["fe_results"][(FE_SELECTOR_AUX_FN, 10)] == FE_REC_0A_VA
        assert rep["fe_results"][(FE_SELECTOR_AUX_FN, 25)] == FE_REC_19_VA

        fe_dy = spec.dy if spec.patch_fe else 0
        assert rep["fe_heights"][(FE_SELECTOR_MAIN_FN, 10)] == (
            3442 + fe_dy,
            3392 + fe_dy,
            compute_fe_field_value(FE_OFFSET_SCALED, FE_SCALE_BASE_20E, fe_dy),
        )
        t_dy = spec.dy if spec.patch_timing else 0
        for m in (MODE_24P_INDEX, MODE_25P_INDEX):
            eh, ev = compute_timing_hv(m, t_dy)
            assert rep["t14_results"][m] == (eh, ev, eh, ev)
            assert rep["t14_vactive"][m] == STOCK_V_ACTIVE + fe_dy

        reports.append(rep)
    return reports


def print_plan() -> None:
    """Print the concise ILCE-6400 patch table across all graduated steps."""
    print(f"ILCE-6400 Squeezed 3:2 Open Gate Plan (av-cam.bin SHA-256: {AVCAM_SHA256[:16]}...)")
    print(f"  reglist col 11 VA: {REGLIST_VA:#x}, fe_rec_0a: {FE_REC_0A_VA:#x}, fe_rec_19: {FE_REC_19_VA:#x}")
    print(f"  timing_ntsc VA: {TIMING_NTSC_VA:#x}, timing_pal VA: {TIMING_PAL_VA:#x}, rt_t14 VA: {RT_T14_VA:#x}")
    for name in ("putback", "test1", "test4", "test3", "test2"):
        s = STEPS[name]
        ys, ysz = compute_sensor_geometry(s.dy if s.patch_sensor else 0)
        fe_dy = s.dy if s.patch_fe else 0
        t_dy = s.dy if s.patch_timing else 0
        h24, v24 = compute_timing_hv(MODE_24P_INDEX, t_dy)
        h25, v25 = compute_timing_hv(MODE_25P_INDEX, t_dy)
        print(
            f"  {name:7s}: dy={s.dy:3d} Y_START={ys:3d} Y_SIZE={ysz:4d} "
            f"FE={3392 + fe_dy:4d} (+0x20e={compute_fe_field_value(FE_OFFSET_SCALED, FE_SCALE_BASE_20E, fe_dy)}) "
            f"24p=({h24},{v24}) [{h24*v24}] 25p=({h25},{v25}) [{h25*v25}]"
        )


def emit_c_header(image: bytes, out_path: Path) -> int:
    """Write ``tools/research/a6400_og_ram_table.h`` with all verified RAM patch entries."""
    step_names = ("putback", "test1", "test4", "test3", "test2", "shrink", "plus24", "plus8")
    by_step = {
        name: build_patch_plan(image, name, verify_stock=True)
        for name in step_names
    }
    rt_by_step = {
        name: build_runtime_timing_targets(name)
        for name in step_names
    }
    base_rom = by_step["putback"]
    base_rt = rt_by_step["putback"]

    rows: List[Tuple[int, int, bytes, bytes, bytes, bytes, bytes, bytes, bytes, bytes, str]] = []
    for idx, p in enumerate(base_rom):
        rows.append((
            p.phys_addr,
            len(p.expected),
            p.expected,
            by_step["test1"][idx].patched,
            by_step["test4"][idx].patched,
            by_step["test3"][idx].patched,
            by_step["test2"][idx].patched,
            by_step["shrink"][idx].patched,
            by_step["plus24"][idx].patched,
            by_step["plus8"][idx].patched,
            f"{p.block} {p.note}",
        ))
    for idx, p in enumerate(base_rt):
        rows.append((
            p.phys_addr,
            len(p.expected),
            p.expected,
            rt_by_step["test1"][idx].patched,
            rt_by_step["test4"][idx].patched,
            rt_by_step["test3"][idx].patched,
            rt_by_step["test2"][idx].patched,
            rt_by_step["shrink"][idx].patched,
            rt_by_step["plus24"][idx].patched,
            rt_by_step["plus8"][idx].patched,
            f"{p.block} {p.note}",
        ))

    def fmt8(b: bytes) -> str:
        padded = b.ljust(8, b"\x00")
        return "{" + ", ".join(f"0x{x:02x}u" for x in padded) + "}"

    liro_hdr = ", ".join(f"0x{x:02x}u" for x in image[:16])
    lines = [
        "/* Auto-generated by tools/research/a6400_open_gate.py --emit-c-header. Do not edit. */",
        "#ifndef A6400_OG_RAM_TABLE_H",
        "#define A6400_OG_RAM_TABLE_H",
        "",
        "#include <stdint.h>",
        "",
        f"#define AVCAM_PHYS_BASE 0x{AVCAM_PHYS_BASE:08X}u",
        f"#define RT_T14_M16_PA   0x{(RT_T14_VA + MODE_24P_INDEX * RT_T14_STRIDE - AVCAM_PHYS_DELTA):08X}u",
        f"#define RT_T14_M17_PA   0x{(RT_T14_VA + MODE_25P_INDEX * RT_T14_STRIDE - AVCAM_PHYS_DELTA):08X}u",
        f"#define RT_T10_M16_PA   0x{(RT_T10_VA + MODE_24P_INDEX * RT_T10_STRIDE - AVCAM_PHYS_DELTA):08X}u",
        f"#define RT_T10_M17_PA   0x{(RT_T10_VA + MODE_25P_INDEX * RT_T10_STRIDE - AVCAM_PHYS_DELTA):08X}u",
        "",
        f"static const uint8_t LIRO_HDR[16] = {{{liro_hdr}}};",
        "",
        "struct og_patch {",
        "    uint32_t pa;",
        "    uint8_t len;",
        "    uint8_t stock[8];",
        "    uint8_t t1[8];",
        "    uint8_t t4[8];",
        "    uint8_t t3[8];",
        "    uint8_t t2[8];",
        "    uint8_t ts[8];",
        "    uint8_t tp24[8];",
        "    uint8_t tp8[8];",
        "};",
        "",
        f"#define N_OG_PATCHES {len(rows)}u",
        "static const struct og_patch OG_PATCHES[N_OG_PATCHES] = {",
    ]
    for pa, length, st, t1, t4, t3, t2, ts, tp24, tp8, desc in rows:
        lines.append(
            f"    {{0x{pa:08X}u, {length}u, {fmt8(st)}, {fmt8(t1)}, {fmt8(t4)}, {fmt8(t3)}, {fmt8(t2)}, {fmt8(ts)}, {fmt8(tp24)}, {fmt8(tp8)}}}, /* {desc} */"
        )
    lines.extend(["};", "", "#endif /* A6400_OG_RAM_TABLE_H */", ""])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return len(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ILCE-6400 squeezed Open Gate offline verifier and planner")
    parser.add_argument("--avcam", type=Path, default=DEFAULT_AVCAM_PATH, help="Path to av-cam-camera-original.bin")
    parser.add_argument("--plan", action="store_true", help="Print the graduated patch table")
    parser.add_argument("--verify", action="store_true", help="Verify stock bytes, Unicorn execution, and putback")
    parser.add_argument("--emit-c-header", type=Path, default=None, help="Write C table header for a6400_og_ram.c")
    args = parser.parse_args(argv)

    if not args.plan and not args.verify and args.emit_c_header is None:
        args.plan = True
        args.verify = True

    if args.plan:
        print_plan()
    if args.emit_c_header is not None:
        image = args.avcam.read_bytes()
        sha256 = hashlib.sha256(image).hexdigest()
        if len(image) != AVCAM_SIZE or sha256 != AVCAM_SHA256:
            raise ValueError(f"Unexpected av-cam.bin: size={len(image)}, sha256={sha256}")
        count = emit_c_header(image, args.emit_c_header)
        print(f"EMIT PASS: wrote {count} patch entries to {args.emit_c_header}")
    if args.verify:
        reports = verify_offline(args.avcam)
        for r in reports:
            h24, v24, _, _ = r["t14_results"][MODE_24P_INDEX]  # type: ignore[index]
            h25, v25, _, _ = r["t14_results"][MODE_25P_INDEX]  # type: ignore[index]
            print(
                f"VERIFY PASS {r['step']:7s}: patches={r['patch_count']:3d} "
                f"Y=({r['y_start']},{r['y_size']}) FE={r['fe_heights'][(FE_SELECTOR_MAIN_FN, 10)]} "
                f"t14_24p=({h24},{v24}) t14_25p=({h25},{v25}) putback=EXACT"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
