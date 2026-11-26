"""Read-only disassembly helper for the body-lens (BL) protocol records.

E0693/E0694 cite exact instructions by address; this tool reproduces them from a
hashed input. It never writes to an image.

Built-in images (paths relative to the repository root):

* ``avcam``  - ``firmware/extracted_a6400/.../nflasha3_unpacked/av-cam.bin``,
  Thumb, ORIL base 0x635C6000 (file offset = address - base);
* ``libobj`` - ``firmware/extracted_a6400/.../nflasha15_unpacked/lib/libObj.so``,
  ARM ELF whose text vaddr equals its file offset, so no base is applied.

Lens and adapter images live outside this repository; pass them explicitly::

    python -m tools.research.bl_disasm dis 0x080078B0 0x40 --file <path> --base 0x08004800

Commands::

    python -m tools.research.bl_disasm hex 0x63F0439B 0x40 --image avcam
    python -m tools.research.bl_disasm dis 0x63855F94 0xA0 --image avcam
    python -m tools.research.bl_disasm xrefs 0x63D12860 --image avcam
    python -m tools.research.bl_disasm str "START UP" --image avcam

Capstone is required for ``dis``/``xrefs``; ``hex`` and ``str`` need only the
standard library.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from project_paths import PROJECT_ROOT

A6400_DEV = (
    PROJECT_ROOT
    / "firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev"
)

IMAGES: dict[str, tuple[Path, int]] = {
    "avcam": (A6400_DEV / "nflasha3_unpacked/av-cam.bin", 0x635C6000),
    "libobj": (A6400_DEV / "nflasha15_unpacked/lib/libObj.so", 0),
}


def _load(args: argparse.Namespace) -> tuple[bytes, int]:
    if args.file:
        path = Path(args.file)
        base = args.base
    else:
        if args.image not in IMAGES:
            sys.exit(f"unknown image {args.image!r}; use --file/--base for external images")
        path, base = IMAGES[args.image]
        if args.base is not None:
            base = args.base
    if not path.exists():
        sys.exit(f"missing input: {path}")
    return path.read_bytes(), base


def _capstone(thumb: bool):
    from capstone import CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, Cs

    return Cs(CS_ARCH_ARM, CS_MODE_THUMB if thumb else CS_MODE_ARM)


def cmd_hex(args: argparse.Namespace) -> None:
    data, base = _load(args)
    off = int(args.value, 0) - base
    chunk = data[off : off + args.length]
    for i in range(0, len(chunk), 16):
        row = chunk[i : i + 16]
        hexpart = " ".join(f"{b:02X}" for b in row)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        print(f"0x{base + off + i:08X}  {hexpart:<47}  {asc}")


def cmd_dis(args: argparse.Namespace) -> None:
    data, base = _load(args)
    addr = int(args.value, 0)
    off = addr - base
    if off < 0 or off >= len(data):
        sys.exit(f"address 0x{addr:08X} outside the image (base 0x{base:08X})")
    md = _capstone(not args.arm)
    # Without skipdata, Capstone's iterator stops at the first undecodable byte,
    # which silently truncates a "sweep the whole image" run. Emit `.byte` for
    # undecodable units instead, so a window always covers its full length.
    md.skipdata = True
    for insn in md.disasm(data[off : off + args.length], addr):
        print(f"0x{insn.address:08X}: {insn.mnemonic:<8} {insn.op_str}")


def cmd_xrefs(args: argparse.Namespace) -> None:
    data, base = _load(args)
    target = int(args.value, 0)
    hits = 0
    for off in range(0, len(data) - 4, 2):
        hw1 = int.from_bytes(data[off : off + 2], "little")
        hw2 = int.from_bytes(data[off + 2 : off + 4], "little")
        if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0xD000:
            s = (hw1 >> 10) & 1
            imm10 = hw1 & 0x3FF
            j1 = (hw2 >> 13) & 1
            j2 = (hw2 >> 11) & 1
            imm11 = hw2 & 0x7FF
            i1 = 1 - (j1 ^ s)
            i2 = 1 - (j2 ^ s)
            imm = (s << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1)
            if s:
                imm -= 1 << 25
            if base + off + 4 + imm == target:
                print(f"BL  0x{base + off:08X}")
                hits += 1
        if int.from_bytes(data[off : off + 4], "little") == target:
            print(f"LIT 0x{base + off:08X}")
            hits += 1
    print(f"{hits} reference(s)")


def cmd_str(args: argparse.Namespace) -> None:
    data, base = _load(args)
    needle = args.value.encode()
    start = 0
    hits = 0
    while hits < 40:
        idx = data.find(needle, start)
        if idx < 0:
            break
        print(f"0x{base + idx:08X}: {data[idx:idx + 64]!r}")
        start = idx + 1
        hits += 1
    print(f"{hits} hit(s)")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Read-only BL image disassembly.")
    ap.add_argument("command", choices=["dis", "hex", "xrefs", "str"])
    ap.add_argument("value", help="address (0x...) or search text")
    ap.add_argument("length", nargs="?", type=lambda s: int(s, 0), default=0x100)
    ap.add_argument("--image", default="avcam", choices=sorted(IMAGES))
    ap.add_argument("--file", help="external image path (lens/adapter firmware)")
    ap.add_argument("--base", type=lambda s: int(s, 0), help="load base for --file")
    ap.add_argument("--arm", action="store_true", help="disassemble as ARM, not Thumb")
    args = ap.parse_args(argv)
    if args.command == "str":
        cmd_str(args)
    elif args.command == "hex":
        cmd_hex(args)
    elif args.command == "dis":
        cmd_dis(args)
    else:
        cmd_xrefs(args)


if __name__ == "__main__":
    main()
