"""Custom OS USB Gate 2: full enumeration plus a real PING/PONG handshake (QEMU only).

The Custom OS has no Sony USB service, so Gate 2 is met with first-party pieces instead:

* ``custom_os/launcher/usb_gift_start.c`` builds the gadgetcore descriptor table and calls
  ``USB_IOC_GADGETCORE_START`` (0x400ce203), which the stock application normally supplies.
* ``custom_os/usb/a6400_vendor_fn.c`` is a function driver for interface 0 that answers a
  vendor IN request (bRequest 1, 4 bytes) with ``PONG`` by queueing a real EP0 request.
* ``custom_os/hybrid/qemu_custom_os_usb_state.py`` boots the Custom OS, initialises the
  controller, starts the table, runs the strict-EP0 host, and records the result.

The static tests check the sources and the ABI mirror; the integration test runs the whole
gate when the WSL/Linux QEMU build, the custom kernel build tree and the built guest
artifacts are present.
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
KERNEL_BUILD = Path(os.environ.get("A6400_KERNEL_BUILD", str(Path.home() / "a6400-kernel/build-custom")))
GIFT = PROJECT_ROOT / "artifacts/tests/custom-os-gift-1/usb_gift_start"
OTG = PROJECT_ROOT / "artifacts/tests/custom-os-usb-otg-3/usb_otg_bringup"
VENDOR = PROJECT_ROOT / "artifacts/tests/custom-os-usbmods-2/a6400_vendor_fn.ko"
NATIVE_AVAILABLE = os.name == "posix" and QEMU.is_file() and KERNEL_BUILD.is_dir()
CUSTOM_OS_AVAILABLE = (PROJECT_ROOT / "custom_os/launcher/usb_gift_start.c").is_file()

EXPECTED_DEVICE = "12010002ff0000404c05a80c000100000001"
EXPECTED_CONFIG = "09021200010100c0320904000000ff000000"


@unittest.skipUnless(CUSTOM_OS_AVAILABLE, "Optional gitignored custom_os tree absent")
class Gate2SourceTests(unittest.TestCase):
    def test_start_helper_uses_the_start_ioctl(self):
        text = (PROJECT_ROOT / "custom_os/launcher/usb_gift_start.c").read_text()
        self.assertIn("0x400ce203UL", text)
        self.assertIn("--offline-qemu-only", text)
        self.assertIn("desc_tbl", text)

    def test_vendor_driver_queues_a_dma_capable_reply(self):
        text = (PROJECT_ROOT / "custom_os/usb/a6400_vendor_fn.c").read_text()
        self.assertIn("usb_gadgetcore_register_driver", text)
        self.assertIn("usb_ep_alloc_request", text)
        self.assertIn("usb_ep_queue", text)
        self.assertIn("kmalloc(4, GFP_ATOMIC)", text)

    def test_abi_mirror_is_checked_against_the_kernel(self):
        header = (PROJECT_ROOT / "custom_os/usb/gadget_desc_abi.h").read_text()
        check = (PROJECT_ROOT / "custom_os/usb/abi_check.c").read_text()
        self.assertIn("a6400_desc_table", header)
        self.assertIn("a6400_start_info", header)
        self.assertIn("SAME_SIZE(usb_gadget_desc_table, a6400_desc_table)", check)
        self.assertIn("SAME_OFFSET(struct usb_gadgetcore_start_info", check)


@unittest.skipUnless(NATIVE_AVAILABLE, "WSL/Linux QEMU, custom kernel build tree or gate artifacts absent")
@unittest.skipUnless(GIFT.is_file() and OTG.is_file() and VENDOR.is_file(),
                     "built guest helpers/modules absent (run the build scripts first)")
class Gate2NativeTests(unittest.TestCase):
    def test_enumeration_and_ping_pong(self):
        out = PROJECT_ROOT / "artifacts/tests" / f"qemu-custom-os-usb-gate2-{int(time.time())}"
        command = [
            sys.executable, "-m", "custom_os.hybrid.qemu_custom_os_usb_state",
            "--out", str(out),
            "--host-attach", "--otg-bringup",
            "--otg-args=--no-teardown", "--otg-hold", "30",
            "--strict-ep0", "--no-stage-debugd",
            "--otg-helper", str(OTG),
            "--gift-start", str(GIFT),
            "--vendor-module", str(VENDOR),
            "--enumerate", "--vendor-ping",
        ]
        result = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True,
                                text=True, timeout=900)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((out / "result.json").read_text())
        enumeration = report["otg"]["enumeration"]
        self.assertEqual(enumeration["device_hex"], EXPECTED_DEVICE)
        self.assertEqual(enumeration["configuration_hex"], EXPECTED_CONFIG)
        self.assertEqual(enumeration["configuration_value"], 1)
        self.assertTrue(enumeration["control_sequence_completed"])
        ping = report["otg"]["vendor_ping"]
        self.assertTrue(ping["ok"], ping)
        self.assertEqual(ping["ascii"], "PONG")
        self.assertIn("state=configured", report["guest"]["udc_after_enum"])
        self.assertIn("PING answered with PONG", report["guest"]["dmesg_after_vendor"])


if __name__ == "__main__":
    unittest.main()
