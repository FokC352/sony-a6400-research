"""Unit and Unicorn regression tests for ILCE-6400 squeezed Open Gate."""
from __future__ import annotations

import unittest

from tools.research.a6400_open_gate import (
    DEFAULT_AVCAM_PATH,
    FE_ALL_OFFSETS,
    FE_SCALE_BASE_20E,
    MODE_24P_INDEX,
    MODE_25P_INDEX,
    STOCK_TIMING_HV,
    STOCK_Y_CENTER_SUM,
    build_patch_plan,
    build_runtime_timing_targets,
    compute_fe_field_value,
    compute_sensor_geometry,
    compute_timing_hv,
    verify_offline,
)


def _has_unicorn() -> bool:
    try:
        import unicorn  # noqa: F401
        return True
    except Exception:
        return False


class TestOpenGateMath(unittest.TestCase):
    def test_sensor_geometry_keeps_vertical_center_and_bayer_parity(self) -> None:
        for dy, exp in ((0, (342, 3396)), (160, (262, 3556)), (312, (186, 3708)), (624, (30, 4020))):
            y_start, y_size = compute_sensor_geometry(dy)
            self.assertEqual((y_start, y_size), exp)
            self.assertEqual(y_start * 2 + y_size, STOCK_Y_CENTER_SUM)
            self.assertEqual(y_start % 4, 2)
        with self.assertRaises(ValueError):
            compute_sensor_geometry(156)

    def test_fe_field_formulas_match_a7iii_design(self) -> None:
        self.assertEqual(len(FE_ALL_OFFSETS), 66)
        self.assertEqual(compute_fe_field_value(0x00A, 3392, 624), 4016)
        self.assertEqual(compute_fe_field_value(0x1E4, 1711, 624), 2023)
        self.assertEqual(compute_fe_field_value(0x250, 848, 624), 1004)
        self.assertEqual(compute_fe_field_value(0x20E, FE_SCALE_BASE_20E, 160), 6092)
        self.assertEqual(compute_fe_field_value(0x20E, FE_SCALE_BASE_20E, 312), 6353)
        self.assertEqual(compute_fe_field_value(0x20E, FE_SCALE_BASE_20E, 624), 6888)

    def test_timing_product_within_0_1_percent(self) -> None:
        for mode_idx in (MODE_24P_INDEX, MODE_25P_INDEX):
            h0, v0 = STOCK_TIMING_HV[mode_idx]
            base_prod = h0 * v0
            for dy in (0, 160, 312, 624):
                h, v = compute_timing_hv(mode_idx, dy)
                rel_err = abs(h * v - base_prod) / base_prod
                self.assertLess(rel_err, 0.001, f"mode={mode_idx} dy={dy} err={rel_err}")

    def test_runtime_t14_targets_cover_both_pairs_and_vactive(self) -> None:
        targets = build_runtime_timing_targets("test2")
        self.assertEqual(len(targets), 6)
        self.assertEqual(
            [t.va for t in targets],
            [0x64201A14, 0x64201A74, 0x64201A8C, 0x64201AEC, 0x64201A5C, 0x64201AD4],
        )


@unittest.skipUnless(DEFAULT_AVCAM_PATH.is_file() and _has_unicorn(), "Requires av-cam.bin and unicorn")
class TestOpenGateRealFirmware(unittest.TestCase):
    def test_patch_counts_and_verify_offline_unicorn(self) -> None:
        image = DEFAULT_AVCAM_PATH.read_bytes()
        self.assertEqual(len(build_patch_plan(image, "test1")), 4 + 132 + 8 + 2 + 6)
        self.assertEqual(len(build_patch_plan(image, "test2")), 4 + 132 + 8 + 2 + 6)

        reports = verify_offline(DEFAULT_AVCAM_PATH)
        by_name = {r["step"]: r for r in reports}
        self.assertEqual((by_name["test1"]["y_start"], by_name["test1"]["y_size"]), (30, 4020))
        self.assertEqual((by_name["test2"]["y_start"], by_name["test2"]["y_size"]), (30, 4020))
        self.assertEqual(by_name["test2"]["t14_results"][MODE_24P_INDEX], (731, 2052, 731, 2052))  # type: ignore[index]
        self.assertEqual(by_name["test2"]["t14_results"][MODE_25P_INDEX], (701, 2052, 701, 2052))  # type: ignore[index]
        self.assertEqual(by_name["test2"]["t14_vactive"][MODE_24P_INDEX], 4000)  # type: ignore[index]
        self.assertEqual(by_name["test2"]["t14_vactive"][MODE_25P_INDEX], 4000)  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
