"""Offline checks for tools.research.usb_did_pid_trace on constructed fixtures.

No Sony bytes are embedded: the BK4 preset and the DID table are built here, so
the tests exercise the lookup contract, not any body's real values.
"""
import struct
import unittest

from src.a6400.execution.backup_service import BK4PresetState
from tools.research.usb_did_pid_trace import (
    NO_UNIT, DidRecord, decode_did_records, resolve_unit, trace,
)


def bk4(units):
    """Build a checksummed BK4 image. ``units`` maps (subsystem, prop) -> bytes."""
    subsystems = max(s for s, _ in units) + 1
    per = {s: max(p for t, p in units if t == s) + 1 for s in {s for s, _ in units}}
    properties = sum(per.values())
    data_offset = 0x100 + 6 * subsystems + 6 * properties
    raw = bytearray(data_offset)
    raw[12:16] = b"BK4\0"
    payload = bytearray()
    base = 0
    for s in range(subsystems):
        count = per.get(s, 0)
        struct.pack_into("<HI", raw, 0x100 + 6 * s, count, base)
        for p in range(count):
            value = units.get((s, p))
            entry = 0x100 + 6 * subsystems + 6 * (base + p)
            if value is None:
                struct.pack_into("<HI", raw, entry, 0, NO_UNIT)
                continue
            # flag byte = size, offset = absolute payload position (original encoding)
            struct.pack_into("<HI", raw, entry, 0, (len(value) << 24) | (data_offset + len(payload)))
            payload += value
        base += count
    raw += payload
    struct.pack_into("<4I", raw, 0x10, subsystems, properties, data_offset, len(payload))
    checksum = (sum(raw[:0x20]) + sum(raw[0x24:])) & 0xFFFFFFFF
    struct.pack_into("<I", raw, 0x20, checksum)
    return BK4PresetState(bytes(raw))


class FakeMemory:
    def __init__(self, words):
        self.words = words

    def read(self, va, count):
        return tuple(self.words[va + 4 * i] for i in range(count))


def record(did, pid_unit, aux_unit):
    return [did, 0, 0, 0, 0, 0, pid_unit, aux_unit]


class DecodeTests(unittest.TestCase):
    def memory(self, table, records):
        words = {}
        for i, va in enumerate(table):
            words[0x1000 + 4 * i] = va
        for va, rec in records.items():
            for i, w in enumerate(rec):
                words[va + 4 * i] = w
        return FakeMemory(words)

    def test_null_entries_are_skipped_and_units_are_read_from_words_6_and_7(self):
        mem = self.memory([0x2000, 0, 0x3000], {0x2000: record(0, 0x00010002, 0x00010000),
                                               0x3000: record(2, NO_UNIT, NO_UNIT)})
        got = decode_did_records(mem.read, 0x1000, 3)
        self.assertEqual(got, [DidRecord(0, 0x2000, 0x00010002, 0x00010000),
                               DidRecord(2, 0x3000, NO_UNIT, NO_UNIT)])

    def test_record_that_does_not_name_its_own_did_fails_closed(self):
        mem = self.memory([0x2000], {0x2000: record(7, 1, 1)})
        with self.assertRaisesRegex(ValueError, "layout not recognised"):
            decode_did_records(mem.read, 0x1000, 1)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.state = bk4({(1, 0): b"\x00\x01", (1, 2): b"\x34\x12", (1, 3): b"DSC\0"})

    def test_two_byte_unit_resolves_to_a_pid_value(self):
        row = resolve_unit(self.state, 0x00010002)
        self.assertEqual((row["status"], row["value"]), ("resolved", "0x1234"))

    def test_no_override_missing_and_non_16_bit_units_are_reported_not_guessed(self):
        self.assertEqual(resolve_unit(self.state, NO_UNIT)["status"], "no_override")
        self.assertEqual(resolve_unit(self.state, 0x00010001)["status"], "not_in_preset")
        self.assertEqual(resolve_unit(self.state, 0x00050000)["status"], "not_in_preset")
        row = resolve_unit(self.state, 0x00010003)
        self.assertEqual(row["status"], "not_a_16_bit_unit")
        self.assertNotIn("value", row)

    def test_trace_pairs_each_did_with_its_pid_and_aux_units(self):
        rows = trace([DidRecord(0x3B, 0x2000, 0x00010002, 0x00010000)], self.state)
        self.assertEqual(rows[0]["did"], "0x3b")
        self.assertEqual(rows[0]["pid"]["value"], "0x1234")
        self.assertEqual(rows[0]["aux"]["value"], "0x0100")


if __name__ == "__main__":
    unittest.main()
