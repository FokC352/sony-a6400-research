"""Tests for CXD90045 P2S 4-wire sensor-bus decoding and IMX284 mode correlation."""
from __future__ import annotations

import unittest

from project_paths import PROJECT_ROOT
from src.a6400.sensor.imx284_p2s import (
    IMX284_CHIP_ID,
    IMX284_COLUMN_BY_ID,
    IMX284_COLUMNS,
    IMX284_MODE_TO_COL,
    Imx284VirtualSensor,
    P2sBurst,
    P2sFrameError,
    decode_p2s_frame,
    encode_p2s_frame,
    extract_avcam_imx284_mode_table,
    parse_sensor_frame_log,
)

TRACE_LOG = (
    PROJECT_ROOT
    / "artifacts/qemu-a6400/imx284-p2s-full-trace-20261120/pinned-preview-context/console.log"
)
EXTRACTED_AVCAM = (
    PROJECT_ROOT
    / "firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin"
)


class Imx284P2sTests(unittest.TestCase):
    def test_roundtrip_and_framing_guards(self):
        bursts = (
            P2sBurst(chip_id=IMX284_CHIP_ID, reg_addr=0x0000, data=bytes([0x04, 0x04, 0x00])),
            P2sBurst(chip_id=IMX284_CHIP_ID, reg_addr=0x000A, data=bytes([0x6A, 0x00, 0x6C, 0x04])),
            P2sBurst(chip_id=IMX284_CHIP_ID, reg_addr=0x00B3, data=bytes([0x90])),
        )
        raw = encode_p2s_frame(bursts)
        self.assertEqual(decode_p2s_frame(raw), bursts)

        with self.assertRaises(P2sFrameError):
            decode_p2s_frame(b"")
        with self.assertRaises(P2sFrameError):
            decode_p2s_frame(b"\x81\x04\x81\x00\x00\x2c")  # missing 0x00 terminator
        with self.assertRaises(P2sFrameError):
            decode_p2s_frame(b"\x82\x04\x81\x00\x00\x2c\x00")  # bad sync byte
        with self.assertRaises(P2sFrameError):
            decode_p2s_frame(b"\x81\x08\x81\x00\x00\x2c\x00")  # truncated payload

    def test_all_13_columns_and_19_modes_uniquely_recognized(self):
        self.assertEqual(len(IMX284_MODE_TO_COL), 19)
        covered_modes = sorted(m for spec in IMX284_COLUMNS for m in spec.modes)
        self.assertEqual(covered_modes, list(range(19)))

        for spec in IMX284_COLUMNS:
            sensor = Imx284VirtualSensor()
            frame = encode_p2s_frame([
                P2sBurst(IMX284_CHIP_ID, 0x0000, bytes([spec.r0000, spec.r0001, spec.r0002])),
                P2sBurst(
                    IMX284_CHIP_ID,
                    0x000A,
                    bytes([
                        spec.xhs_default & 0xFF,
                        spec.xhs_default >> 8,
                        spec.xvs_default & 0xFF,
                        spec.xvs_default >> 8,
                    ]),
                ),
                P2sBurst(IMX284_CHIP_ID, 0x000F, bytes([spec.r000f])),
                P2sBurst(IMX284_CHIP_ID, 0x0017, bytes([spec.r0017])),
                P2sBurst(IMX284_CHIP_ID, 0x0420, bytes([spec.r0420, spec.r0421])),
                P2sBurst(IMX284_CHIP_ID, 0x0803, bytes([spec.r0803])),
                P2sBurst(IMX284_CHIP_ID, 0x0924, bytes([spec.r0924])),
            ])
            sensor.apply_frame(frame)
            matched = sensor.recognize_column()
            self.assertIsNotNone(matched, f"Column {spec.col} failed to match")
            self.assertEqual(matched.col, spec.col)
            self.assertEqual(sensor.candidate_modes(), spec.modes)

    @unittest.skipUnless(TRACE_LOG.is_file(), "Retained QEMU P2S trace log absent")
    def test_retained_qemu_liveview_trace_correlates_to_column_3(self):
        frames = parse_sensor_frame_log(TRACE_LOG.read_text(encoding="utf-8", errors="replace"))
        self.assertEqual(len(frames), 32)
        self.assertTrue(all(f.channel == 1 for f in frames))

        sensor = Imx284VirtualSensor()
        streaming_transitions: list[tuple[int, bool]] = []
        prev_stream = None
        for f in frames:
            sensor.apply_frame(f.payload)
            exp = sensor.exposure_state()
            if exp.slvs_ec_streaming != prev_stream:
                streaming_transitions.append((f.seq, exp.slvs_ec_streaming))
                prev_stream = exp.slvs_ec_streaming

        col = sensor.recognize_column()
        self.assertIsNotNone(col)
        self.assertEqual(col.col, 3)
        self.assertEqual(col.modes, (4, 5))
        self.assertEqual((col.active_width, col.active_height), (1996, 570))
        # seq=8 puts SLVS-EC into sync-reset (0x80) and seq=9 resumes streaming (0x90)
        self.assertIn((8, False), streaming_transitions)
        self.assertIn((9, True), streaming_transitions)
        final_exp = sensor.exposure_state()
        self.assertFalse(final_exp.standby)
        self.assertTrue(final_exp.slvs_ec_streaming)
        self.assertEqual(final_exp.xhs, 0x006A)
        self.assertEqual(final_exp.xvs, 0x046C)
        self.assertEqual(final_exp.pgc_gain, 0x038F)
        self.assertEqual(final_exp.wb_clamp_r, 0x08A4)

    @unittest.skipUnless(EXTRACTED_AVCAM.is_file(), "Extracted av-cam.bin absent")
    def test_avcam_static_mode_table_matches_column_fingerprints(self):
        table = extract_avcam_imx284_mode_table(EXTRACTED_AVCAM.read_bytes())
        self.assertEqual(len(table[0]), 125)
        for col_idx, regs in table.items():
            spec = IMX284_COLUMN_BY_ID[col_idx]
            self.assertEqual(regs[0x0000], spec.r0000)
            self.assertEqual(regs[0x0001], spec.r0001)
            self.assertEqual(regs[0x0002], spec.r0002)
            self.assertEqual(regs[0x000F], spec.r000f)
            self.assertEqual(regs[0x0017], spec.r0017)
            self.assertEqual(regs[0x0803], spec.r0803)
            self.assertEqual(regs[0x0924], spec.r0924)


if __name__ == "__main__":
    unittest.main()
