"""Unit and firmware ground-truth tests for the 13-camera signature scanner."""
from __future__ import annotations

from pathlib import Path
import struct
import tempfile
import unittest

from project_paths import PROJECT_ROOT
from tools.research.elf32_lite import Elf32Image, extract_masked_thumb2, scan_masked
from tools.research.signature_scanner import (
    OFFICIAL_13_MODELS,
    SignatureSpec,
    emit_c_headers,
    extract_signature_command,
    scan_body,
    scan_roots,
)


def _build_synthetic_thumb_elf(
    code: bytes,
    symbols: list[tuple[str, int, int, int]] | None = None,
) -> bytes:
    """Construct a minimal little-endian ARM ELF32 shared object in memory."""
    text_vaddr = 0x1000
    text_off = 0x100
    text_size = len(code)

    sym_list = [("", 0, 0, 0)] + list(symbols or [])
    strtab = bytearray(b"\x00")
    sym_bytes = bytearray()
    for name, val, size, info in sym_list:
        if name:
            name_off = len(strtab)
            strtab.extend(name.encode("ascii") + b"\x00")
        else:
            name_off = 0
        sym_bytes.extend(struct.pack("<IIIBBH", name_off, val, size, info, 0, 1 if val else 0))

    shstrtab = bytearray(b"\x00.text\x00.symtab\x00.strtab\x00.shstrtab\x00")
    sh_text_name = shstrtab.index(b".text")
    sh_symtab_name = shstrtab.index(b".symtab")
    sh_strtab_name = shstrtab.index(b".strtab")
    sh_shstr_name = shstrtab.index(b".shstrtab")

    symtab_off = (text_off + text_size + 3) & ~3
    strtab_off = symtab_off + len(sym_bytes)
    shstr_off = strtab_off + len(strtab)
    shoff = (shstr_off + len(shstrtab) + 3) & ~3

    blob = bytearray(shoff + 5 * 40)
    # ELF header (52 bytes)
    struct.pack_into(
        "<16sHHIIIIIHHHHHH",
        blob,
        0,
        b"\x7fELF\x01\x01\x01" + b"\x00" * 9,
        3,  # ET_DYN
        40,  # EM_ARM
        1,
        0,
        52,
        shoff,
        0x05000000,
        52,
        32,
        1,
        40,
        5,
        4,
    )
    # Single PT_LOAD RX covering header + .text
    struct.pack_into("<8I", blob, 52, 1, 0, 0, 0, text_off + text_size, text_off + text_size, 5, 0x1000)

    blob[text_off : text_off + text_size] = code
    blob[symtab_off : symtab_off + len(sym_bytes)] = sym_bytes
    blob[strtab_off : strtab_off + len(strtab)] = strtab
    blob[shstr_off : shstr_off + len(shstrtab)] = shstrtab

    # Section headers: 0=null, 1=.text, 2=.symtab, 3=.strtab, 4=.shstrtab
    struct.pack_into("<10I", blob, shoff + 40 * 1, sh_text_name, 1, 6, text_vaddr, text_off, text_size, 0, 0, 4, 0)
    struct.pack_into("<10I", blob, shoff + 40 * 2, sh_symtab_name, 2, 0, 0, symtab_off, len(sym_bytes), 3, 1, 4, 16)
    struct.pack_into("<10I", blob, shoff + 40 * 3, sh_strtab_name, 3, 0, 0, strtab_off, len(strtab), 0, 0, 1, 0)
    struct.pack_into("<10I", blob, shoff + 40 * 4, sh_shstr_name, 3, 0, 0, shstr_off, len(shstrtab), 0, 0, 1, 0)
    return bytes(blob)


def _thumb_insns_unique(bl_word1: int, bl_word2: int) -> bytes:
    """12 Thumb insns (push; movw; ldr; bl target; ...)."""
    halfwords = [
        0xB510,          # push {r4, lr}
        0x202A,          # movs r0, #42
        0x4604,          # mov r4, r0
        bl_word1 & 0xFFFF,
        bl_word2 & 0xFFFF,
        0x2800,          # cmp r0, #0
        0xD001,          # beq +2
        0x2107,          # movs r1, #7
        0x4420,          # add r0, r4
        0x3009,          # adds r0, #9
        0x3403,          # adds r4, #3
        0x4620,          # mov r0, r4
        0xBD10,          # pop {r4, pc}
    ]
    return b"".join(struct.pack("<H", hw) for hw in halfwords)


class SignatureScannerUnitTests(unittest.TestCase):
    def test_synthetic_unique_ambiguous_absent_and_extract_roundtrip(self):
        fn_a = _thumb_insns_unique(0xF001, 0xF805)  # bl #offset1
        fn_b = _thumb_insns_unique(0xF002, 0xFA10)  # identical logic, relocated bl offset!
        filler = b"\x00\xbf" * 8  # nop padding

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body_a = root / "bodyA"
            body_b = root / "bodyB"
            body_dup = root / "bodyDup"
            for d in (body_a, body_b, body_dup):
                d.mkdir()

            elf_a_path = body_a / "viewUnified2.so"
            elf_a_path.write_bytes(
                _build_synthetic_thumb_elf(
                    filler + fn_a + filler,
                    [("target_fn", 0x1011, len(fn_a), (1 << 4) | 2)],
                )
            )
            (body_b / "viewUnified2.so").write_bytes(
                _build_synthetic_thumb_elf(filler * 2 + fn_b + filler)
            )
            # Duplicate function -> scanner must refuse with AMBIGUOUS_REFUSED!
            (body_dup / "viewUnified2.so").write_bytes(
                _build_synthetic_thumb_elf(filler + fn_a + filler + fn_b)
            )

            extracted = extract_signature_command(elf_a_path, 0x1010, insn_count=12, structural=False)
            self.assertTrue(extracted["unique_in_source"])

            specs = [
                SignatureSpec(
                    name="test.thumb_fn",
                    pack="mlv2",
                    binary="viewUnified2.so",
                    kind="masked_thumb2",
                    pattern_hex=extracted["pattern_hex"],
                    mask_hex=extracted["mask_hex"],
                    define_name="TEST_THUMB_FN",
                ),
                SignatureSpec(
                    name="test.sym_fn",
                    pack="mlv2",
                    binary="viewUnified2.so",
                    kind="elf_sym",
                    symbol="target_fn",
                    define_name="TEST_SYM_FN",
                ),
                SignatureSpec(
                    name="test.missing_sym",
                    pack="mlv2",
                    binary="viewUnified2.so",
                    kind="elf_sym",
                    symbol="nonexistent_sym",
                    define_name="TEST_MISSING",
                ),
            ]

            res_a = scan_body("SYN-A", body_a, specs)
            res_b = scan_body("SYN-B", body_b, specs)
            res_dup = scan_body("SYN-DUP", body_dup, specs)

            self.assertEqual(res_a["anchors"][0]["status"], "UNIQUE_VERIFIED")
            self.assertEqual(res_a["anchors"][0]["value_int"], 0x1010)
            self.assertEqual(res_a["anchors"][1]["status"], "UNIQUE_VERIFIED")
            self.assertEqual(res_a["anchors"][1]["value_int"], 0x1011)
            self.assertEqual(res_a["anchors"][2]["status"], "ABSENT")

            # Relocated bl target still resolves uniquely in body B.
            self.assertEqual(res_b["anchors"][0]["status"], "UNIQUE_VERIFIED")
            self.assertEqual(res_b["anchors"][0]["value_int"], 0x1020)

            # Fail closed on 2+ hits.
            self.assertEqual(res_dup["anchors"][0]["status"], "AMBIGUOUS_REFUSED")
            self.assertIsNone(res_dup["anchors"][0]["value_int"])
            self.assertEqual(res_dup["anchors"][0]["match_count"], 2)

            # Header emission contains only UNIQUE_VERIFIED #defines.
            hdrs = emit_c_headers(
                {"bodies": {"SYN-B": res_b, "SYN-DUP": res_dup}}, root / "headers"
            )
            self.assertEqual(len(hdrs), 2)
            hdr_b = (root / "headers" / "syn_b_signatures.h").read_text(encoding="utf-8")
            self.assertIn("#define TEST_THUMB_FN 0x00001020u", hdr_b)
            self.assertIn("UNRESOLVED TEST_SYM_FN: ABSENT", hdr_b)
            hdr_dup = (root / "headers" / "syn_dup_signatures.h").read_text(encoding="utf-8")
            self.assertIn("AMBIGUOUS_REFUSED", hdr_dup)

    def test_official_13_list_completeness_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = scan_roots([Path(tmp)], pack="mlv2")
            self.assertEqual(len(report["official_13_models"]), 13)
            self.assertEqual(set(OFFICIAL_13_MODELS), set(report["official_13_models"]))
            self.assertEqual(len(report["not_unpacked_locally"]), 13)


FW_ROOT = PROJECT_ROOT / "firmware"
LOCAL_FIRMWARE_PRESENT = (FW_ROOT / "extracted_a6400").is_dir() and (
    FW_ROOT / "extracted_zve10_v203_linuxnames"
).is_dir()


@unittest.skipUnless(LOCAL_FIRMWARE_PRESENT, "Requires extracted local firmware trees")
class SignatureScannerFirmwareGroundTruthTests(unittest.TestCase):
    def test_ground_truth_and_zve10_multibody_scan(self):
        report = scan_roots([FW_ROOT], pack="all")
        self.assertIn("ILCE-6400", report["unpacked_present"])
        self.assertIn("ZV-E10", report["unpacked_present"])
        for model in report["unpacked_present"]:
            info = report["bodies"][model]
            self.assertEqual(info["ground_truth_mismatches"], [], f"{model}: {info['ground_truth_mismatches']}")
            self.assertGreater(info["counts"]["UNIQUE_VERIFIED"], 15, model)
            self.assertEqual(info["counts"]["AMBIGUOUS_REFUSED"], 0, model)

        zve_anchors = {a["name"]: a for a in report["bodies"]["ZV-E10"]["anchors"]}
        self.assertEqual(zve_anchors["mlv2.set_root"]["value_int"], 0x00861EE8)
        self.assertEqual(zve_anchors["mlv2.refresh"]["value_int"], 0x00862320)
        self.assertEqual(zve_anchors["mlv2.open_item"]["value_int"], 0x0086292C)
        self.assertEqual(zve_anchors["mtf2.vt_menu_tab"]["value_int"], 0x00C09E28)


if __name__ == "__main__":
    unittest.main()
