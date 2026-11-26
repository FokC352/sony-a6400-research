"""Unit and WSL2 persistent Ghidra daemon integration tests for symbol_index."""
from __future__ import annotations

import os
from pathlib import Path
import struct
import tempfile
import unittest

from project_paths import PROJECT_ROOT
from tools.research.symbol_index import (
    daemon_start,
    daemon_status,
    decompile_function,
    index_elf,
    lookup_symbol_or_va,
    open_db,
    query_callers_callees,
    symbolize_offset,
)
from tests.test_signature_scanner import _build_synthetic_thumb_elf


def _thumb_callee_caller() -> bytes:
    """Assemble two adjacent Thumb functions: caller at 0x1000 calls callee at 0x1010."""
    # caller at 0x1000..0x100c: push {lr}; movs r0, #7; bl 0x1010 (pc=0x1008 -> off=+8); pop {pc}
    # callee at 0x1010..0x1016: adds r0, #1; bx lr
    halfwords = [
        0xB500,  # 0x1000: push {lr}
        0x2007,  # 0x1002: movs r0, #7
        0xF000,  # 0x1004: bl 0x1010 (+8 -> J1=1 J2=1 imm11=4)
        0xF804,  # 0x1006
        0xBD00,  # 0x1008: pop {pc}
        0xBF00,  # 0x100a: nop
        0xBF00,  # 0x100c: nop
        0xBF00,  # 0x100e: nop
        0x3001,  # 0x1010: adds r0, #1
        0x4770,  # 0x1012: bx lr
    ]
    return b"".join(struct.pack("<H", hw) for hw in halfwords)


class SymbolIndexUnitTests(unittest.TestCase):
    def test_build_lookup_callgraph_and_symbolize_roundtrip(self):
        code = _thumb_callee_caller()
        blob = _build_synthetic_thumb_elf(
            code,
            symbols=[
                ("caller_fn", 0x1001, 10, (1 << 4) | 2),
                ("osal_snd_msg", 0x1011, 4, (1 << 4) | 2),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            elf_path = root / "libTest.so"
            elf_path.write_bytes(blob)
            db_path = root / "test.sqlite3"
            conn = open_db(db_path)
            try:
                res1 = index_elf(conn, elf_path, body="TEST_BODY")
                self.assertEqual(res1["status"], "indexed")
                self.assertEqual(res1["symbols"], 2)
                self.assertEqual(res1["ipc_sites"], 1)

                # Re-indexing same SHA-256 is skipped deterministically.
                res2 = index_elf(conn, elf_path, body="TEST_BODY")
                self.assertEqual(res2["status"], "already_indexed")

                # Symbol lookup by name & enclosing VA.
                by_name = lookup_symbol_or_va(conn, "caller_fn", body="TEST_BODY")
                self.assertEqual(len(by_name["symbols"]), 1)
                self.assertEqual(by_name["symbols"][0]["address"], "0x00001001")

                by_va = lookup_symbol_or_va(conn, "0x1004", body="TEST_BODY")
                self.assertEqual(by_va["symbols"][0]["name"], "caller_fn")

                # Callers & callees.
                callers = query_callers_callees(conn, "callers", "osal_snd_msg", binary="libTest.so")
                self.assertEqual(len(callers), 1)
                self.assertEqual(callers[0]["caller_name"], "caller_fn")

                callees = query_callers_callees(conn, "callees", "caller_fn", binary="libTest.so")
                self.assertEqual(len(callees), 1)
                self.assertEqual(callees[0]["callee_name"], "osal_snd_msg")

                # IPC constant r0 = 7 extraction.
                ipc_row = conn.execute("SELECT caller_name, callee_name, queue_or_cmd FROM ipc_sites").fetchone()
                self.assertEqual(ipc_row["caller_name"], "caller_fn")
                self.assertEqual(ipc_row["callee_name"], "osal_snd_msg")
                self.assertEqual(ipc_row["queue_or_cmd"], 7)

                # Symbolize known and unknown VAs.
                self.assertEqual(symbolize_offset(conn, "libTest.so", 0x1004), "libTest.so!caller_fn+0x4")
                self.assertEqual(symbolize_offset(conn, "libTest.so", 0x9000), "libTest.so!sub_00009000")
            finally:
                conn.close()


A6400_LIBOBJ = (
    PROJECT_ROOT
    / "firmware"
    / "extracted_a6400"
    / "firmware.tar_unpacked"
    / "0700_part_image"
    / "dev"
    / "nflasha15_unpacked"
    / "lib"
    / "libObj.so"
)
A6400_VU2 = A6400_LIBOBJ.parent / "viewUnified2.so"


@unittest.skipUnless(
    os.name == "posix" and A6400_LIBOBJ.is_file() and A6400_VU2.is_file(),
    "Requires WSL2/POSIX + extracted_a6400 firmware",
)
class SymbolIndexGhidraDaemonTests(unittest.TestCase):
    def test_persistent_daemon_running_and_decompiles_libobj_and_vu2(self):
        status = daemon_status()
        if not status.get("running"):
            status = daemon_start()
        self.assertTrue(status.get("running"), f"Persistent Ghidra daemon not running: {status}")

        res_lo = decompile_function(A6400_LIBOBJ, "_ZN12EventManager4pushEP5Eventb")
        self.assertTrue(res_lo.get("ok"))
        self.assertIn("EventManager", res_lo.get("func_name", ""))
        self.assertTrue(len(res_lo.get("c_code", "")) > 20)

        res_vu = decompile_function(A6400_VU2, "0x5ab655")
        self.assertTrue(res_vu.get("ok"))
        self.assertTrue(len(res_vu.get("c_code", "")) > 20)


if __name__ == "__main__":
    unittest.main()
