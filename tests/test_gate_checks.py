"""Unit checks for the QEMU gate markers and the panel pixel ROI (no QEMU needed).

``custom_os.hybrid.gate_checks`` is the single judge for Phase A and Phase B, so its acceptance
rules are tested directly: the camera's register values must pass, an OSD/YUV layer that is
switched off must fail, and the pixel-level ROI must accept a composed bars+UI frame while
rejecting a blank one.
"""

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from project_paths import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))
from custom_os.hybrid import gate_checks  # noqa: E402

VPHREG_HW = """\
VPH clk=00000000 en144=01000232 osdbpp=00000000 | ch51 ctrl=00000003 st=00000000 addr=14e90000 lb=00000400 skip=00000080 lines=00239 | ch53 ctrl=00000003 addr=23a80000
VPH_140_BEFORE=30000000
"""


def write_png(path: Path, width: int, height: int, pixels: bytes):
    stride = width * 3
    raw = b''.join(b'\x00' + pixels[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + \
            struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    path.write_bytes(b'\x89PNG\r\n\x1a\n'
                     + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(raw, 6))
                     + chunk(b'IEND', b''))


def composed_frame(bars=True, ui=True):
    width, height = 640, 480
    luma = [235, 210, 170, 145, 106, 81, 41, 16]
    out = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            value = luma[x * 8 // width] if bars else 0
            off = (y * width + x) * 3
            out[off:off + 3] = bytes((value, value, value))
    if ui:
        for y in range(8, 42):
            for x in range(7, 47):
                off = (y * width + x) * 3
                out[off:off + 3] = bytes((255, 136, 38))
    return width, height, bytes(out)


class VphItemTests(unittest.TestCase):
    def test_camera_values_pass(self):
        item = gate_checks.vph_item(VPHREG_HW)
        self.assertTrue(item['ok'], item)
        self.assertEqual(item['en144'], '01000232')
        self.assertEqual(item['r140'], '30000000')

    def test_legacy_forced_bit0_still_passes(self):
        text = VPHREG_HW.replace('en144=01000232', 'en144=01000233')
        self.assertTrue(gate_checks.vph_item(text)['ok'])

    def test_osd_disabled_fails(self):
        text = VPHREG_HW.replace('en144=01000232', 'en144=01000230')
        item = gate_checks.vph_item(text)
        self.assertFalse(item['ok'], item)
        self.assertFalse(item['en144_hw'])

    def test_unprogrammed_ch51_fails(self):
        text = VPHREG_HW.replace('ch51 ctrl=00000003 st=00000000 addr=14e90000',
                                 'ch51 ctrl=00000002 st=00000000 addr=00000000')
        self.assertFalse(gate_checks.vph_item(text)['ok'])

    def test_unsane_panel_stage_value_fails(self):
        text = VPHREG_HW.replace('VPH_140_BEFORE=30000000', 'VPH_140_BEFORE=c0000000')
        item = gate_checks.vph_item(text)
        self.assertFalse(item['ok'], item)
        self.assertFalse(item['r140_ok'])


class PngRoiTests(unittest.TestCase):
    def test_composed_bars_and_ui_pass(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / 'artifacts/tests') as tmp:
            path = Path(tmp) / 'ok.png'
            write_png(path, *composed_frame())
            roi = gate_checks.png_roi(path)
        self.assertTrue(roi['ok'], roi)
        self.assertGreaterEqual(roi['bar_contrast'], 40)
        self.assertGreaterEqual(roi['accent_pixels'], 200)

    def test_blank_panel_fails(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / 'artifacts/tests') as tmp:
            path = Path(tmp) / 'blank.png'
            write_png(path, *composed_frame(bars=False, ui=False))
            roi = gate_checks.png_roi(path)
        self.assertFalse(roi['ok'], roi)

    def test_bars_without_ui_fail(self):
        """The live view alone is not the Custom OS UI (the negative-control signature)."""
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / 'artifacts/tests') as tmp:
            path = Path(tmp) / 'bars.png'
            write_png(path, *composed_frame(ui=False))
            roi = gate_checks.png_roi(path)
        self.assertFalse(roi['ok'], roi)
        self.assertLess(roi['accent_pixels'], 50)
        self.assertGreaterEqual(roi['bar_contrast'], 40)


if __name__ == '__main__':
    unittest.main()
