"""Constructed-fixture tests for qemu_headless_receipt and qemu_boot_inputs_17part."""
from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.research import qemu_boot_inputs, qemu_boot_inputs_17part, qemu_headless_receipt


class TestQemuHeadlessReceipt(unittest.TestCase):
    def test_find_sequencer_table_and_ram_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            avcam = Path(tmp) / "av-cam.bin"
            avcam.write_bytes(b"\x00" * 0x100 + qemu_headless_receipt.STARTUP_TABLE_SIG + b"\x00" * 0x40)
            tva, _ = qemu_headless_receipt.find_sequencer_table_va(avcam)
            self.assertEqual(tva, qemu_headless_receipt.AVCAM_LOAD_VA + 0x100)

            ram = bytearray(0x200)
            struct.pack_into("<4I", ram, 0x48, 1, tva, 0x2B, 42)
            res = qemu_headless_receipt.find_sequencer_in_ram(bytes(ram), tva, base_pa=0x04300000)
            self.assertIsNotNone(res)
            assert res is not None
            self.assertEqual(res["step"], 42)
            self.assertEqual(res["index_pa"], 0x04300040 + 0x14)

    def test_parse_logs_and_ppm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ppm = Path(tmp) / "screen.ppm"
            ppm.write_bytes(b"P6\n2 2\n255\n" + b"\x00\x00\x00\x01\x02\x03" * 2)
            disp = qemu_headless_receipt.parse_ppm(ppm)
            self.assertTrue(disp["present"])
            self.assertEqual((disp["width"], disp["height"]), (2, 2))
            self.assertEqual(disp["nonzero_bytes"], 6)

        parsed = qemu_headless_receipt.parse_logs(
            "Kernel command line: console=ttyAM0\nuart3: ttyAM1 at MMIO\n",
            "[DMP] ppf-transfer addr=0x23a80000 n=614400\n"
            "[VPH] ch53 base=0x23a80000 ctrl=0x2 lb=0x500 lines=480 en144=0x230\n"
            "[VPH] ch51 base=0x14fbc000 lb=0x400 skip=0x80 lines=570 en144=0x1000233\n"
            "cxd90045.f290.unmodelled: unimplemented device read (size 4, offset 0x2900030)\n",
            d_unimp=True,
        )
        self.assertTrue(parsed["boot"]["kernel_booted"])
        self.assertTrue(parsed["boot"]["vph_ch53_enabled"])
        self.assertTrue(parsed["boot"]["vph_ch51_enabled"])
        self.assertEqual(parsed["unmodelled"]["distinct_pairs"], 1)


@unittest.skipIf(shutil.which("mkfs.fat") is None or shutil.which("mcopy") is None, "dosfstools/mtools required")
class TestQemuBootInputs17Part(unittest.TestCase):
    def test_prepare_and_verify_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump = root / "dump"
            dump.mkdir()
            # Build minimal FAT images for nflasha2 (/setting) and nflasha3 (/system)
            fat2 = dump / "nflasha2.bin"
            fat3 = dump / "nflasha3.bin"
            for img, size in ((fat2, 512 * 1024), (fat3, 512 * 1024)):
                img.write_bytes(b"\x00" * size)
                subprocess.run(["mkfs.fat", "--invariant", str(img)], check=True, capture_output=True)

            (root / "Backup.bin").write_bytes(struct.pack("<I", 0x02E80000) + b"\x00" * 60)
            (root / "kemco.txt").write_bytes(b"kemco console=ttynull root=/dev/ram0\n")
            (root / "vmlinux.bin").write_bytes(b"ARM\x00" * 32)
            (root / "initrd.img").write_bytes(b"INITRD" * 16)
            (root / "av-cam.bin").write_bytes(b"ORIL" * 32)
            for name in ("Backup.bin", "kemco.txt"):
                subprocess.run(["mcopy", "-o", "-i", str(fat2), str(root / name), f"::/{name}"], check=True)
            for name in ("vmlinux.bin", "initrd.img", "av-cam.bin"):
                subprocess.run(["mcopy", "-o", "-i", str(fat3), str(root / name), f"::/{name}"], check=True)

            # Build SDM 82461.00 header for nflasha2 (at 0x10000) and nflasha3 (at 0x90000)
            header = bytearray(0x10000)
            header[:8] = b"82461.00"
            struct.pack_into("<I", header, 8, 3)
            struct.pack_into("<4I", header, 32, 0x10000, 0, 0, 0)
            struct.pack_into("<4I", header, 48, 0x10000, 512 * 1024, 0, 0)
            struct.pack_into("<4I", header, 64, 0x90000, 512 * 1024, 0, 0)
            hdr_path = root / "sdm-head.bin"
            hdr_path.write_bytes(bytes(header))

            out_dir = root / "out"
            qemu_boot_inputs_17part.prepare(dump, hdr_path, out_dir, target_id="ilce_6400a")
            verified = qemu_boot_inputs.verify(out_dir)
            self.assertEqual(verified["status"], "completed")
            self.assertEqual(verified["target_id"], "ilce_6400a")
