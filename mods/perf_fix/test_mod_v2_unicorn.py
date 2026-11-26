"""Offline Unicorn test of the SMOD v2 PerfFix build (no camera).

Usage: python mods/perf_fix/test_mod_v2_unicorn.py BUILD_DIR
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import unicorn as uc
from unicorn import arm_const as A

BUILD_DIR = Path(sys.argv[1])
mod = (BUILD_DIR / "PerfFix.mod").read_bytes()

magic, ver, flags, name, code_size, fnv, bss, on_off, off_off, hooks = struct.unpack_from(
    "<III32s6I", mod
)
assert magic == 0x444F4D53, f"bad magic {magic:#x}"
assert ver == 2, f"bad ver {ver}"
assert name.rstrip(b"\0") == b"PerfFix", name
assert bss == 32, f"unexpected bss {bss}"
assert hooks == 7, f"expected 7 hooks, got {hooks}"
assert (flags & 4) != 0, "expected FLAG_RELOC (4)"

expected_hooks = [
    (0x060E81D4, bytes.fromhex("2de9f04782b002af06468846")),
    (0x060FFB10, bytes.fromhex("2de9f04100f5b67500af0446")),
    (0x060FFE42, bytes.fromhex("2de9f04700f5b678044600af")),
    (0x061325C8, bytes.fromhex("f0b583b000afc0f800330446")),
    (0x06100576, bytes.fromhex("2de9f0479a46d0f89c3100af")),
    (0x06100FFC, bytes.fromhex("2de9f041044600af0d469046")),
    (0x0610285A, bytes.fromhex("2de9f04782b002af04461546")),
]
hook_entries = []
for i, (exp_va, exp_bytes) in enumerate(expected_hooks):
    off = 68 + i * 40
    target_va, orig_len, fix_off, orig_bytes, tmpl_bytes = struct.unpack_from(
        "<IBB2x16s16s", mod, off
    )
    assert target_va == exp_va, f"hook {i} bad va {target_va:#x}"
    assert orig_len == 12, f"hook {i} bad orig_len {orig_len}"
    assert orig_bytes[:12] == exp_bytes, f"hook {i} bad bytes {orig_bytes[:12].hex()}"
    hook_entry_off = struct.unpack_from("<I", tmpl_bytes, fix_off)[0]
    assert (hook_entry_off & 1) == 1, f"hook {i} must be Thumb: {hook_entry_off:#x}"
    hook_entries.append(hook_entry_off)

code_off = 68 + hooks * 40
code = mod[code_off : code_off + code_size]

rlc_off = code_off + code_size
rlc_magic, rlc_libs, rlc_count = struct.unpack_from("<IHH", mod, rlc_off)
assert rlc_magic == 0x31434C52, f"expected RLC1 trailer, got {rlc_magic:#x}"
assert rlc_libs == 1 and rlc_count == 8, f"expected 1 lib / 8 words, got {rlc_libs}/{rlc_count}"

BASE = 0x40AB3000
STACK = 0x40FE0000
LR_STOP = 0x40FF0000
LIBOBJ_PAGES = [0x060E8000, 0x060FF000, 0x06100000, 0x06101000, 0x06102000, 0x06132000, 0x06308000]
OBJ_BUF = 0x41000000
WS_BUF = 0x41002000

mu = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB)
mu.mem_map(BASE, 0x10000)
mu.mem_map(STACK - 0x4000, 0x8000)
mu.mem_map(LR_STOP, 0x1000)
mu.mem_map(OBJ_BUF, 0x4000)
for p in LIBOBJ_PAGES:
    mu.mem_map(p, 0x1000)

mu.mem_write(BASE, code)
mu.mem_write(BASE + code_size, b"\x00" * bss)

# Stub WidgetSystem::sys_getInstance at 0x06308D30: ldr r0, [pc, #0]; bx lr; .word WS_BUF
mu.mem_write(0x06308D30, bytes.fromhex("00487047") + struct.pack("<I", WS_BUF))
mu.mem_write(WS_BUF + 0x480, struct.pack("<I", 1))  # 30 fps default

# Stub resume targets
mu.mem_write(0x060E81E0, bytes.fromhex("02b0bde8f087"))  # SetKeyRepeat
mu.mem_write(0x060FFB1C, bytes.fromhex("bde8f081"))      # startAnimate
mu.mem_write(0x060FFE4E, bytes.fromhex("bde8f087"))      # initAnimate
mu.mem_write(0x061325D4, bytes.fromhex("03b0f0bd"))      # requestAnimation
mu.mem_write(0x06100582, bytes.fromhex("bde8f087"))      # reqScrollTo(dir)
mu.mem_write(0x06101008, bytes.fromhex("bde8f081"))      # setScrollAreaPos
mu.mem_write(0x06102866, bytes.fromhex("02b0bde8f087"))  # reqScrollTo(iib)

def call(addr: int, r0=0, r1=0, r2=0, r3=0, stack_args=()):
    mu.reg_write(A.UC_ARM_REG_SP, STACK)
    for idx, val in enumerate(stack_args):
        mu.mem_write(STACK + idx * 4, struct.pack("<I", val))
    mu.reg_write(A.UC_ARM_REG_R0, r0)
    mu.reg_write(A.UC_ARM_REG_R1, r1)
    mu.reg_write(A.UC_ARM_REG_R2, r2)
    mu.reg_write(A.UC_ARM_REG_R3, r3)
    mu.reg_write(A.UC_ARM_REG_LR, LR_STOP | 1)
    mu.emu_start(addr, LR_STOP, count=10000)
    return mu.reg_read(A.UC_ARM_REG_R0)

# Enable PerfFix -> WidgetSystem +0x480 becomes 0 (60 fps step)
assert call(BASE + on_off) == 0
assert struct.unpack("<I", mu.mem_read(WS_BUF + 0x480, 4))[0] == 0

# 1) SetKeyRepeat
call(BASE + hook_entries[0], 0x15010060, 0, 500, 150)
assert mu.reg_read(A.UC_ARM_REG_R2) == 60
assert mu.reg_read(A.UC_ARM_REG_R3) == 25

# 2) startAnimate (12 -> 4 frames, smooth & fast)
mu.mem_write(OBJ_BUF + 0x170, struct.pack("<I", 12))
call(BASE + hook_entries[1], OBJ_BUF, 1)
assert struct.unpack("<I", mu.mem_read(OBJ_BUF + 0x170, 4))[0] == 4

# 3) initAnimate (15 -> 4 frames)
call(BASE + hook_entries[2], OBJ_BUF, 1, 100, 15)
assert mu.reg_read(A.UC_ARM_REG_R3) == 4

# 4) requestAnimation (9 -> 3 frames)
mu.mem_write(OBJ_BUF + 0x170, struct.pack("<I", 9))
call(BASE + hook_entries[3], OBJ_BUF, 1, 0x3F800000, 0, stack_args=(0, 0))
assert struct.unpack("<I", mu.mem_read(OBJ_BUF + 0x170, 4))[0] == 3

# 5) GEN_GridList boost (0.20f -> 0.65f, anim stays 1)
mu.mem_write(OBJ_BUF + 0x364, struct.pack("<I", 0x3E4CCCCD))
call(BASE + hook_entries[5], OBJ_BUF, 3, 0, 1)
assert struct.unpack("<I", mu.mem_read(OBJ_BUF + 0x364, 4))[0] == 0x3F266666
assert mu.reg_read(A.UC_ARM_REG_R3) == 1

# Disable PerfFix -> WidgetSystem +0x480 restored to 1
assert call(BASE + off_off) == 0
assert struct.unpack("<I", mu.mem_read(WS_BUF + 0x480, 4))[0] == 1
print("PerfFix 60fps-ready Unicorn test PASS")
