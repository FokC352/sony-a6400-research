"""Unit and native WSL2 QEMU v7 TCG plugin tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from tests.test_signature_scanner import _build_synthetic_thumb_elf
from tools.research.qemu_tcg_trace import (
    DEFAULT_PLUGIN_SO,
    build_plugin,
    format_plugin_arg,
    parse_proc_maps,
    symbolize_trace,
)
from tools.research.symbol_index import index_elf, open_db
from tools.research.toolchain import toolchain_config

QEMU_VERSION = toolchain_config()["qemu"]["version"]
QEMU_DIR = Path(
    os.environ.get("A6400_QEMU_DIR", str(Path.home() / f"sony-a6400-qemu-{QEMU_VERSION}"))
)
QEMU_BIN = QEMU_DIR / "build" / "qemu-system-arm"
NATIVE_TCG_AVAILABLE = os.name == "posix" and QEMU_BIN.is_file() and shutil.which("gcc") is not None


def _build_arm_tcg_stub() -> bytes:
    """ARM stub at 0x00108000: calls helper @ 0x00108020, writes MMIO 0xF3420100, loops."""
    # Offsets from 0x00108000:
    # 0x00: ldr r0, [pc, #0x14]  (lit 0x12345678 @ 0x1c -> wait put pool at +0x38)
    # Better: explicit literal pool offsets after all 12 instructions (0x30).
    code: list[int] = []
    pool: list[int] = []
    fixups: list[tuple[int, int]] = []

    def ldr_lit(rd: int, val: int) -> None:
        pidx = len(pool)
        pool.append(val & 0xFFFFFFFF)
        fixups.append((len(code), pidx))
        code.append(0xE59F0000 | (rd << 12))

    # Helper function will be placed at +0x20 (8 instructions into image):
    # Entry at 0x00108000 (indices 0..7):
    ldr_lit(0, 0x12345678)       # 0x00
    ldr_lit(1, 0x9ABCDEF0)       # 0x04
    ldr_lit(2, 0xF3420100)       # 0x08 (cxd90014-regfile AudioIp +0x100)
    ldr_lit(3, 0xCAFE0001)       # 0x0c
    code.append(0xE5823000)      # 0x10: str r3, [r2] (1st MMIO write)
    code.append(0xE5823000)      # 0x14: str r3, [r2] (consecutive poll write -> coalesce)
    code.append(0xEB000000)      # 0x18: bl +0x20 (pc=0x20 -> target=0x20!)
    code.append(0xEAFFFFFE)      # 0x1c: b .

    # Target subroutine at +0x20 (VA 0x00108020):
    code.append(0xE12FFF1E)      # 0x20: bx lr

    pool_start = len(code) * 4
    for cidx, pidx in fixups:
        imm12 = (pool_start + pidx * 4) - (cidx * 4 + 8)
        code[cidx] |= imm12
    return b"".join(struct.pack("<I", w) for w in (*code, *pool))


class QemuTcgTraceUnitTests(unittest.TestCase):
    def test_format_plugin_arg_and_maps_symbolization(self):
        arg = format_plugin_arg(
            Path("/tmp/libtcg_trace.so"),
            out_jsonl=Path("/tmp/out.jsonl"),
            probes=["hit1:0x5ab654+0x155e6000"],
            derefs=["hit1:r0+0x10:16"],
            mem_ranges=["0xf2403000:0xf2404000"],
            ring=128,
            dump_ring=True,
            exit_on_hit=True,
        )
        self.assertIn("probe=hit1:0x5ab654+0x155e6000", arg)
        self.assertIn("deref=hit1:r0+0x10:16", arg)
        self.assertIn("dump_ring=1", arg)
        self.assertIn("exit_on_hit=1", arg)

        maps = (
            "155e6000-15e92000 r-xp 00000000 fc:0f 123 /usr/lib/viewUnified2.so\n"
            "15e9a000-160ed000 rw-p 008ac000 fc:0f 123 /usr/lib/viewUnified2.so\n"
        )
        regions = parse_proc_maps(maps)
        self.assertEqual(len(regions), 2)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            elf_path = root / "viewUnified2.so"
            elf_path.write_bytes(
                _build_synthetic_thumb_elf(
                    b"\x00\xb5\x00\xbd" * 8,
                    symbols=[("set_root", 0x1005, 8, (1 << 4) | 2)],
                )
            )
            db_path = root / "idx.sqlite3"
            conn = open_db(db_path)
            try:
                index_elf(conn, elf_path, body="ILCE-6400", include_edges=False)
            finally:
                conn.close()

            jsonl = root / "trace.jsonl"
            jsonl.write_text(
                json.dumps(
                    {
                        "type": "probe_hit",
                        "id": "p1",
                        "pc": f"0x{0x155e6000 + 0x1008:08x}",
                        "regs": {"lr": f"0x{0x155e6000 + 0x1004:08x}"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            maps_file = root / "maps.txt"
            maps_file.write_text(maps, encoding="utf-8")
            events = symbolize_trace(jsonl, maps_path=maps_file, db_path=db_path)
            self.assertEqual(events[0]["pc_sym"], "viewUnified2.so!set_root+0x4")
            self.assertEqual(events[0]["lr_sym"], "viewUnified2.so!set_root")


@unittest.skipUnless(NATIVE_TCG_AVAILABLE, "Requires WSL/Linux gcc and sony-a6400 QEMU build")
class QemuNativeTcgPluginTests(unittest.TestCase):
    def test_compile_and_trace_probe_mmio_and_ring_on_sony_a6400(self):
        with tempfile.TemporaryDirectory(prefix="a6400-tcg-test-") as tmp:
            root = Path(tmp)
            so_path = build_plugin(DEFAULT_PLUGIN_SO)
            self.assertTrue(so_path.is_file())

            stub = root / "stub.bin"
            stub.write_bytes(_build_arm_tcg_stub())
            trace_out = root / "trace.jsonl"

            plugin_arg = format_plugin_arg(
                so_path,
                out_jsonl=trace_out,
                probes=["sub:0x00108020"],
                mem_ranges=["0xf3420000:0xf3428000"],
                ring=64,
                dump_ring=True,
                exit_on_hit=True,
            )
            cmd = [
                str(QEMU_BIN),
                "-M",
                "sony-a6400",
                "-smp",
                "1",
                "-kernel",
                str(stub),
                "-display",
                "none",
                "-monitor",
                "none",
                "-serial",
                "null",
                "-net",
                "none",
                "-no-reboot",
                "-plugin",
                plugin_arg,
                "-d",
                "plugin",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            events = symbolize_trace(trace_out)

            hits = [e for e in events if e.get("type") == "probe_hit"]
            mems = [e for e in events if e.get("type") == "mem"]
            rings = [e for e in events if e.get("type") == "ring"]

            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["regs"]["r0"], "0x12345678")
            self.assertEqual(hits[0]["regs"]["r1"], "0x9abcdef0")
            self.assertEqual(hits[0]["regs"]["lr"], "0x0010801c")
            self.assertTrue(any(m["pa"] == "0xf3420100" and m["val"] == "0xcafe0001" for m in mems))
            self.assertTrue(any(r["pc"] == "0x00108000" for r in rings))


if __name__ == "__main__":
    unittest.main()
