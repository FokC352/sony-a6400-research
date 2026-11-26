"""Unit and native WSL2 QEMU VMState snapshot/instant-resume tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import tempfile
import time
import unittest

from project_paths import PROJECT_ROOT
from tools.research.qemu_boot_probe import Qmp
from tools.research.qemu_snapshot import (
    _build_env,
    check_manifest,
    load_live,
    parse_hmp_result,
    save_live,
    sha256_file,
    validate_tag,
)
from tools.research.toolchain import toolchain_config

QEMU_VERSION = toolchain_config()["qemu"]["version"]
QEMU_DIR = Path(
    os.environ.get("A6400_QEMU_DIR", str(Path.home() / f"sony-a6400-qemu-{QEMU_VERSION}"))
)
QEMU_BIN = QEMU_DIR / "build" / "qemu-system-arm"
QEMU_IMG = QEMU_DIR / "build" / "qemu-img"
NATIVE_SNAPSHOT_AVAILABLE = os.name == "posix" and QEMU_BIN.is_file() and QEMU_IMG.is_file()


def _build_arm_snapshot_stub() -> bytes:
    """Assemble a self-contained ARMv7 bare-metal kernel for savevm/loadvm testing.

    Phase 1 writes known non-zero values to RAM and sony-a6400 MMIO devices, sets
    PA 0x00200004 = 0x11223344, and spins waiting for PA 0x00200008 != 0.
    Phase 2 overwrites RAM and those same MMIO devices with different values and
    sets PA 0x00200004 = 0x99887766, then spins forever.
    """
    code: list[int] = []
    pool: list[int] = []
    fixups: list[tuple[int, int]] = []  # (code_idx, pool_idx)

    def ldr_lit(rd: int, value: int) -> None:
        pool_idx = len(pool)
        pool.append(value & 0xFFFFFFFF)
        fixups.append((len(code), pool_idx))
        code.append(0xE59F0000 | (rd << 12))

    def str_word(rd: int, rn: int) -> None:
        code.append(0xE5800000 | (rn << 16) | (rd << 12))

    def ldr_word(rd: int, rn: int) -> None:
        code.append(0xE5900000 | (rn << 16) | (rd << 12))

    phase1_writes = (
        (0x00200000, 0xCAFEBABE),  # RAM0
        (0xF2403008, 0x12345678),  # cxd4115-timer ch0 cmp
        (0xF3420100, 0xA5A55A5A),  # cxd90014-regfile AudioIp +0x100
        (0xF3015020, 0xDEADBEEF),  # cxd90045-camcore RF register +0x5020
        (0xF3441220, 0x00500000),  # cxd90014-vph ch0 base
        (0xF2910040, 0x000000AA),  # cxd90014-gpio port0 out
        (0x00200004, 0x11223344),  # phase1 ready flag
    )
    for addr, val in phase1_writes:
        ldr_lit(0, addr)
        ldr_lit(1, val)
        str_word(1, 0)

    ldr_lit(2, 0x00200008)
    wait_idx = len(code)
    ldr_word(3, 2)
    code.append(0xE3530000)  # cmp r3, #0
    # beq wait_idx: target = wait_idx, current pc = (wait_idx + 2) + 2
    code.append(0x0A000000 | ((-4) & 0xFFFFFF))

    phase2_writes = (
        (0x00200000, 0xBAD00001),
        (0xF2403008, 0xBAD00002),
        (0xF3420100, 0xBAD00003),
        (0xF3015020, 0xBAD00004),
        (0xF3441220, 0xBAD00005),
        (0xF2910040, 0x00000055),
        (0x00200004, 0x99887766),  # phase2 mutated flag
    )
    for addr, val in phase2_writes:
        ldr_lit(0, addr)
        ldr_lit(1, val)
        str_word(1, 0)

    code.append(0xEAFFFFFE)  # b .

    pool_start_bytes = len(code) * 4
    for code_idx, pool_idx in fixups:
        target_bytes = pool_start_bytes + pool_idx * 4
        pc_bytes = code_idx * 4 + 8
        imm12 = target_bytes - pc_bytes
        if not 0 <= imm12 < 4096:
            raise AssertionError(f"Literal offset out of range: {imm12}")
        code[code_idx] |= imm12

    return b"".join(struct.pack("<I", word) for word in (*code, *pool))


def _gdb_write_u32(sock_path: Path, addr: int, value: int) -> None:
    """Write a little-endian 32-bit word via QEMU GDB remote serial protocol."""
    data_hex = struct.pack("<I", value).hex()
    payload = f"M{addr:x},4:{data_hex}".encode("ascii")
    checksum = sum(payload) & 0xFF
    pkt = b"$" + payload + f"#{checksum:02x}".encode("ascii")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    try:
        sock.connect(str(sock_path))
        sock.sendall(pkt)
        reply = b""
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and b"OK" not in reply:
            chunk = sock.recv(256)
            if not chunk:
                break
            reply += chunk
        if b"OK" not in reply:
            raise RuntimeError(f"Unexpected GDB RSP write reply: {reply!r}")
    finally:
        sock.close()


def _xp_word(qmp: Qmp, paddr: int) -> int:
    """Read one 32-bit physical word via QMP HMP 'xp'."""
    out = qmp.request("human-monitor-command", {"command-line": f"xp /1wx 0x{paddr:x}"})
    match = re.search(r":\s*0x([0-9a-fA-F]+)", out)
    if not match:
        raise RuntimeError(f"Unable to parse xp output for 0x{paddr:x}: {out!r}")
    return int(match.group(1), 16)


class QemuSnapshotUnitTests(unittest.TestCase):
    def test_validate_tag_accepts_safe_names_and_rejects_bad(self):
        for good in ("ready", "snap_01-a.b", "A"):
            self.assertEqual(validate_tag(good), good)
        for bad in ("", "../bad", "has space", "a" * 33, "bad;cmd", "a/b"):
            with self.assertRaises(ValueError):
                validate_tag(bad)

    def test_parse_hmp_result_fails_closed_on_error_markers(self):
        self.assertEqual(parse_hmp_result("info snapshots", "ID TAG\n1 ready\n"), "ID TAG\n1 ready")
        for err in (
            "Error while writing VM state: Invalid argument",
            "Device 'cxd' does not have the requested snapshot",
            "Snapshot 'missing' does not exist",
        ):
            with self.assertRaises(RuntimeError):
                parse_hmp_result("savevm test", err)

    def test_check_manifest_verifies_hash_and_machine_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            qcow = bundle / "snapshot.qcow2"
            qcow.write_bytes(b"QCOW-TEST-DATA")
            digest = sha256_file(qcow)
            manifest = {
                "schema_version": 1,
                "tag": "ready",
                "snapshot_qcow2": "snapshot.qcow2",
                "snapshot_qcow2_sha256": digest,
                "machine_env": {"REAR_CONTROLS": "1", "LIVEVIEW_SOURCE": "bars"},
            }
            (bundle / "snapshot-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            loaded, path, tag = check_manifest(
                bundle, expected_env={"REAR_CONTROLS": "1", "LIVEVIEW_SOURCE": "bars"}
            )
            self.assertEqual(tag, "ready")
            self.assertEqual(path, qcow.resolve())
            self.assertEqual(loaded["snapshot_qcow2_sha256"], digest)

            with self.assertRaises(ValueError):
                check_manifest(bundle, expected_env={"REAR_CONTROLS": "0"})

            qcow.write_bytes(b"CORRUPTED")
            with self.assertRaises(ValueError):
                check_manifest(bundle)

    def test_build_env_sets_snapshot_and_loadvm_flags(self):
        env = _build_env(
            inputs=None,
            run_dir=Path("/tmp/run"),
            qmp_sock=Path("/tmp/qmp.sock"),
            serial_sock=Path("/tmp/ser.sock"),
            machine_env={"REAR_CONTROLS": "1"},
            snapshot_qcow=Path("/tmp/snap.qcow2"),
            loadvm_tag="ready",
        )
        self.assertEqual(env["SNAPSHOT_OVERLAY"], str(Path("/tmp/snap.qcow2")))
        self.assertEqual(env["LOADVM"], "ready")
        self.assertEqual(env["REAR_CONTROLS"], "1")


@unittest.skipUnless(NATIVE_SNAPSHOT_AVAILABLE, "Requires WSL/Linux sony-a6400 QEMU build")
class QemuNativeSnapshotRoundtripTests(unittest.TestCase):
    EXPECTED_P1 = {
        0x00200000: 0xCAFEBABE,
        0x00200004: 0x11223344,
        0xF2403008: 0x12345678,
        0xF3420100: 0xA5A55A5A,
        0xF3015020: 0xDEADBEEF,
        0xF3441220: 0x00500000,
        0xF2910040: 0x000000AA,
    }
    EXPECTED_P2 = {
        0x00200000: 0xBAD00001,
        0x00200004: 0x99887766,
        0xF2403008: 0xBAD00002,
        0xF3420100: 0xBAD00003,
        0xF3015020: 0xBAD00004,
        0xF3441220: 0xBAD00005,
        0xF2910040: 0x00000055,
    }

    def _spawn_qemu(
        self,
        qcow: Path,
        stub: Path,
        qmp_sock: Path,
        gdb_sock: Path | None,
        *,
        loadvm_tag: str | None = None,
    ) -> tuple[subprocess.Popen, Qmp]:
        cmd = [
            str(QEMU_BIN),
            "-M",
            "sony-a6400",
            "-smp",
            "1",
            "-kernel",
            str(stub),
            "-drive",
            f"if=sd,id=emmc,file={qcow},format=qcow2",
            "-display",
            "none",
            "-monitor",
            "none",
            "-serial",
            "null",
            "-net",
            "none",
            "-no-reboot",
            "-qmp",
            f"unix:{qmp_sock},server=on,wait=off",
        ]
        if gdb_sock is not None:
            cmd.extend(["-gdb", f"unix:{gdb_sock},server=on,wait=off"])
        if loadvm_tag is not None:
            cmd.extend(["-loadvm", loadvm_tag])
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read().decode("utf-8", errors="replace") if proc.stdout else ""
                if proc.stdout is not None:
                    proc.stdout.close()
                raise RuntimeError(f"QEMU exited early ({proc.returncode}): {out}")
            if qmp_sock.exists():
                return proc, Qmp(qmp_sock, deadline)
            time.sleep(0.05)
        proc.terminate()
        if proc.stdout is not None:
            proc.stdout.close()
        raise TimeoutError("Timed out waiting for QMP socket")

    def _close_proc(self, proc: subprocess.Popen, qmp: Qmp | None) -> None:
        if qmp is not None:
            try:
                qmp.quit(proc, timeout=5)
            except Exception:
                pass
            qmp.close()
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        if proc.stdout is not None:
            proc.stdout.close()

    def test_savevm_loadvm_and_fresh_loadvm_preserve_ram_and_mmio(self):
        # Keep Unix domain sockets on Linux tmpfs (/tmp), never Windows DrvFS (/mnt/c).
        with (
            tempfile.TemporaryDirectory(prefix="a6400-snap-test-") as tmp,
            tempfile.TemporaryDirectory(prefix="a6400-sock-") as sock_tmp,
        ):
            root = Path(tmp)
            sock_dir = Path(sock_tmp)
            qcow = root / "state.qcow2"
            stub = root / "stub.bin"
            stub.write_bytes(_build_arm_snapshot_stub())
            subprocess.run(
                [str(QEMU_IMG), "create", "-f", "qcow2", str(qcow), "64M"],
                check=True,
                capture_output=True,
            )

            qmp_sock = sock_dir / "qmp1.sock"
            gdb_sock = sock_dir / "gdb1.sock"
            proc, qmp = self._spawn_qemu(qcow, stub, qmp_sock, gdb_sock)
            try:
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    if _xp_word(qmp, 0x00200004) == 0x11223344:
                        break
                    time.sleep(0.02)
                self.assertEqual(_xp_word(qmp, 0x00200004), 0x11223344)
                qmp.request("stop")
                for addr, expected in self.EXPECTED_P1.items():
                    self.assertEqual(
                        _xp_word(qmp, addr), expected, f"Phase 1 mismatch at 0x{addr:08x}"
                    )

                save_res = save_live(qmp, "snap1")
                self.assertIn("snap1", save_res["snapshots"])

                _gdb_write_u32(gdb_sock, 0x00200008, 1)
                qmp.request("cont")
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    if _xp_word(qmp, 0x00200004) == 0x99887766:
                        break
                    time.sleep(0.02)
                self.assertEqual(_xp_word(qmp, 0x00200004), 0x99887766)
                qmp.request("stop")
                for addr, expected in self.EXPECTED_P2.items():
                    self.assertEqual(
                        _xp_word(qmp, addr), expected, f"Phase 2 mismatch at 0x{addr:08x}"
                    )

                load_live(qmp, "snap1")
                self.assertEqual(_xp_word(qmp, 0x00200008), 0)
                for addr, expected in self.EXPECTED_P1.items():
                    self.assertEqual(
                        _xp_word(qmp, addr), expected, f"Live loadvm mismatch at 0x{addr:08x}"
                    )
            finally:
                self._close_proc(proc, qmp)

            t0 = time.monotonic()
            proc2, qmp2 = self._spawn_qemu(
                qcow, stub, sock_dir / "qmp2.sock", None, loadvm_tag="snap1"
            )
            resume_elapsed = time.monotonic() - t0
            try:
                qmp2.request("stop")
                self.assertEqual(_xp_word(qmp2, 0x00200008), 0)
                for addr, expected in self.EXPECTED_P1.items():
                    self.assertEqual(
                        _xp_word(qmp2, addr), expected, f"Fresh -loadvm mismatch at 0x{addr:08x}"
                    )
                self.assertLess(resume_elapsed, 3.0)
            finally:
                self._close_proc(proc2, qmp2)


if __name__ == "__main__":
    unittest.main()
