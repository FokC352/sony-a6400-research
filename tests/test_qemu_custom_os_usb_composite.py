"""Custom OS USB composite (Gate 3 guest side): debugger + data + UVC-shaped function.

Interfaces: MI_00 vendor debugger (bulk IN/OUT + PING), MI_01 data (bulk IN/OUT +
interrupt IN), MI_02 UVC VideoControl, MI_03 UVC VideoStreaming (bulk IN frame on alt 1).

The static tests check the sources; the integration test boots the Custom OS in QEMU, starts
the composite table, enumerates, sends the vendor PING and a UVC VS_PROBE GET_CUR, switches
the VideoStreaming interface to alt 1, then interleaves host tokens on all six endpoints and
finishes with SET_CONFIGURATION(0) + soft-disconnect, requiring the pull-up to drop and the
host to be NAKed afterwards.
"""

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from project_paths import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))

QEMU_DIR = Path(os.environ.get("A6400_QEMU_DIR", str(Path.home() / "sony-a6400-qemu-11.1.3")))
QEMU = QEMU_DIR / "build/qemu-system-arm"
GIFT = PROJECT_ROOT / "artifacts/tests/custom-os-gift-3/usb_gift_start"
OTG = PROJECT_ROOT / "artifacts/tests/custom-os-usb-otg-3/usb_otg_bringup"
COMPOSITE = PROJECT_ROOT / "artifacts/tests/custom-os-usbmods-9/a6400_composite_fn.ko"
FRAME = PROJECT_ROOT / "tools/native_camera/test_pattern_720p.jpg"
NATIVE_AVAILABLE = os.name == "posix" and QEMU.is_file()
CUSTOM_OS_AVAILABLE = (PROJECT_ROOT / "custom_os/usb/a6400_composite_fn.c").is_file()


@unittest.skipUnless(CUSTOM_OS_AVAILABLE, "Optional gitignored custom_os tree absent")
class CompositeSourceTests(unittest.TestCase):
    def test_start_helper_builds_uvc_interfaces(self):
        text = (PROJECT_ROOT / "custom_os/launcher/usb_gift_start.c").read_text()
        self.assertIn("g_uvc_vc_desc", text)
        self.assertIn("g_uvc_vs_desc", text)
        self.assertIn("0x86, 0x02, 512", text)          # VideoStreaming bulk IN
        self.assertIn("g_interfaces[3].uc_num_if_tables = 2", text)
        self.assertIn("g_config.uc_num_interfaces = 4", text)

    def test_driver_registers_uvc_and_answers_probe(self):
        text = (PROJECT_ROOT / "custom_os/usb/a6400_composite_fn.c").read_text()
        self.assertIn('a6400_uvc_vs_driver.interface = 3', text)
        self.assertIn('a6400_uvc_vc_driver.interface = 2', text)
        self.assertIn("a6400_uvc_probe[26]", text)
        self.assertIn("UVC GET_CUR request", text)


@unittest.skipUnless(NATIVE_AVAILABLE, "WSL/Linux QEMU build absent")
@unittest.skipUnless(GIFT.is_file() and OTG.is_file() and COMPOSITE.is_file(),
                     "built composite artifacts absent (run the build scripts first)")
class CompositeNativeTests(unittest.TestCase):
    def test_interleaved_composite_traffic(self):
        out = PROJECT_ROOT / "artifacts/tests" / f"qemu-custom-os-usb-uvc-{int(time.time())}"
        command = [
            sys.executable, "-m", "custom_os.hybrid.qemu_custom_os_usb_state",
            "--out", str(out),
            "--host-attach", "--otg-bringup", "--otg-args=--no-teardown",
            "--otg-hold", "55", "--strict-ep0", "--strict-composite",
            "--no-stage-debugd", "--otg-helper", str(OTG),
            "--gift-start", str(GIFT), "--composite",
            "--composite-module", str(COMPOSITE),
            "--enumerate", "--vendor-ping", "--set-interface", "3:1", "--uvc-probe",
            "--uvc-stream", "--parallel-cycles", "2",
            "--parallel-eps", "2:in,3:out,4:in,5:out,6:in",
            "--unconfigure", "--soft-disconnect",
            "--gift-hold", "46", "--teardown-wait", "4",
        ]
        result = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True,
                                text=True, timeout=900)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((out / "result.json").read_text())
        otg = report["otg"]
        configuration = otg["enumeration"]["configuration_hex"]
        self.assertTrue(otg["enumeration"]["control_sequence_completed"])
        self.assertEqual(configuration.count("0904"), 5, configuration)
        self.assertEqual(configuration.count("0705"), 6, configuration)
        self.assertTrue(otg["vendor_ping"]["ok"])

        probe = otg["uvc_probe"]
        self.assertEqual(probe["bytes"], 26, probe)
        self.assertEqual(probe["bFormatIndex"], 1, probe)
        self.assertEqual(probe["frame_interval"], 333333, probe)
        self.assertTrue(otg["set_interface"]["ok"])

        stream = otg["uvc_stream"]
        frame = FRAME.read_bytes()
        self.assertTrue(stream["exact"], stream)
        self.assertEqual(stream["bytes"], len(frame))
        self.assertEqual(stream["expected_sha256"], stream["sha256"])
        self.assertEqual(stream["jpeg_soi"], "ffd8")
        self.assertEqual(stream["jpeg_eoi"], "ffd9")
        self.assertGreaterEqual(len(stream["interleaved"]), 2)
        self.assertEqual(stream["interleaved"][0]["debug_hex"][:16], "4442473a30303030")

        for token in otg["parallel"]["tokens"]:
            if token["phys"] == 2:
                self.assertTrue(token["reply"].startswith("IN 2 4442473a"), token)
            elif token["phys"] == 4:
                self.assertTrue(token["reply"].startswith("IN 4 44415441"), token)
            elif token["phys"] == 6:
                self.assertTrue(token["reply"].startswith("IN 6 4952513a"), token)
            else:
                self.assertTrue(token["reply"].startswith(f"ACK {token['phys']} 32"), token)

        self.assertTrue(otg["unconfigure"]["ok"])
        self.assertFalse(int(report["registers"]["after_soft_disconnect"]["DCTL"]["value"], 16)
                         & (1 << 31))
        self.assertEqual(int(report["registers"]["after_soft_disconnect"]["DALEPENA"]["value"],
                             16), 0)
        self.assertEqual(otg["disconnected_probe"], "NAK 2")

        dmesg = report["guest"]["dmesg_comp_final"]
        self.assertIn("out DBG len=32", dmesg)
        self.assertIn("out DATA len=32", dmesg)
        self.assertIn("UVC GET_CUR request 81 answered (26 bytes)", dmesg)
        self.assertIn("uvc set_cur stage=2 status=0 actual=26", dmesg)
        self.assertIn("uvc frame complete status=0 actual=29452/29452", dmesg)
        self.assertIn("uvc vs start alt=1", dmesg)
        self.assertIn("uvc vc stop", dmesg)


if __name__ == "__main__":
    unittest.main()
