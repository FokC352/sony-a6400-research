"""QEMU ``sony-a6400`` opt-in SDIO Wi-Fi card: positive CMD5/CMD3/CMD7/CMD52 contract.

The mmc1 host (``0xF0301000``, IRQ 221) has no card by default, which reproduces the camera's
E0410 ``mmcio_mount failed``.  With ``-M sony-a6400,sdio-wifi=on`` a minimal SDIO card answers the
enumeration sequence instead, so the driver's ``mmc_io_detect`` (CMD5) and selection succeed.
The card is QEMU's generic SD model plus the SDIO commands; the BCM4339's exact OCR/CCCR values
are not captured, so the responses are the conservative defaults.
"""

import unittest

from tests.test_qemu_emmc import NATIVE_AVAILABLE
from tests import test_qemu_liveview

MMC1 = 0xF0301000

SDHC_ARGUMENT = 0x08
SDHC_TRNMOD = 0x0C
SDHC_RSPREG0 = 0x10
SDHC_PRNSTS = 0x24
SDHC_PWRCON = 0x29
SDHC_CLKCON = 0x2C
SDHC_NORINTSTS = 0x30
SDHC_NORINTSTSEN = 0x34
SDHC_NORINTSIGEN = 0x38

NIS_ERR = 0x8000
CARD_PRESENT = 0x00010000


@unittest.skipUnless(NATIVE_AVAILABLE, 'Optional WSL/Linux sony-a6400 QEMU build absent')
class QemuSdioCardTests(unittest.TestCase):
    start_qtest = test_qemu_liveview.QemuLiveViewPipelineTests.start_qtest
    _stop_proc = test_qemu_liveview.QemuLiveViewPipelineTests._stop_proc
    qtest = test_qemu_liveview.QemuLiveViewPipelineTests.qtest

    def setUp(self):
        self.proc, _ = self.start_qtest('sony-a6400,sdio-wifi=on')
        self.pending = [b'']
        q = lambda text: self.qtest(self.proc, self.pending, text)
        q(f'writeb {MMC1 + SDHC_PWRCON:#x} 0x0f')
        q(f'writew {MMC1 + SDHC_CLKCON:#x} 0x0007')
        q(f'writel {MMC1 + SDHC_NORINTSTSEN:#x} 0xffffffff')
        q(f'writel {MMC1 + SDHC_NORINTSIGEN:#x} 0xffffffff')
        q(f'writel {MMC1 + SDHC_NORINTSTS:#x} 0xffffffff')   # W1C start clean

    def command(self, cmdidx, arg=0, flags=0x0a):
        q = lambda text: self.qtest(self.proc, self.pending, text)
        q(f'writel {MMC1 + SDHC_ARGUMENT:#x} {arg:#x}')
        q(f'writel {MMC1 + SDHC_TRNMOD:#x} {(cmdidx << 8 | flags) << 16:#x}')
        return q(f'readl {MMC1 + SDHC_RSPREG0:#x}')

    def test_card_is_present(self):
        present = self.qtest(self.proc, self.pending, f'readl {MMC1 + SDHC_PRNSTS:#x}')
        self.assertTrue(present & CARD_PRESENT, f'card-present bit missing: 0x{present:08x}')

    def test_cmd5_answers_ready_and_one_function(self):
        r4 = self.command(5)
        self.assertTrue(r4 & 0x80000000, f'R4 ready bit clear: 0x{r4:08x}')
        self.assertEqual(r4 & 0x7, 1, f'R4 function count: 0x{r4:08x}')
        # mmc_drv.ko's mmc_io_detect rejects an R4 word whose bits 28-30 are clear
        # (tst r3,#0x70000000; beq fail at 0xee74), so the I/O-function field must be non-zero.
        self.assertTrue(r4 & 0x70000000, f'R4 I/O-function field clear: 0x{r4:08x}')
        status = self.qtest(self.proc, self.pending, f'readw {MMC1 + SDHC_NORINTSTS:#x}')
        self.assertFalse(status & NIS_ERR, f'CMD5 reported an error: 0x{status:04x}')

    def test_common_cis_parses_at_its_pointer(self):
        """After the CCCR walk the driver reads the common CIS byte-by-byte (E0689 trace)."""
        read = lambda addr: addr << 9
        self.command(5)
        r6 = self.command(3)
        self.command(7, arg=r6 & 0xffff0000)
        pointer = ((self.command(52, read(0x09)) & 0xff) |
                   (self.command(52, read(0x0a)) & 0xff) << 8 |
                   (self.command(52, read(0x0b)) & 0xff) << 16)
        self.assertEqual(pointer, 0x001000, f'CIS pointer: 0x{pointer:06x}')
        self.assertEqual(self.command(52, read(pointer)) & 0xff, 0x20,
                         'common CIS must start with CISTPL_MANFID')
        self.assertEqual(self.command(52, read(pointer + 1)) & 0xff, 0x04,
                         'CISTPL_MANFID body length')
        # Walk the tuple stream the way the driver does and require a well-formed END.
        addr, tuples = pointer, []
        for _ in range(8):
            code = self.command(52, read(addr)) & 0xff
            if code == 0xff:
                tuples.append(code)
                break
            length = self.command(52, read(addr + 1)) & 0xff
            tuples.append(code)
            addr += 2 + length
        self.assertIn(0x20, tuples)
        self.assertIn(0x21, tuples)
        self.assertIn(0x22, tuples)
        self.assertEqual(tuples[-1], 0xff, f'CIS has no CISTPL_END: {tuples}')

    def test_cmd3_cmd7_cmd52_enumeration(self):
        self.command(5)
        r6 = self.command(3)
        self.assertEqual(r6 & 0xffff0000, 0x00010000, f'R6 RCA: 0x{r6:08x}')
        self.command(7, arg=r6 & 0xffff0000)          # SELECT_CARD with that RCA
        r5 = self.command(52)                          # IO_RW_DIRECT, read CCCR 0x00
        # CCCR/SDIO revision: 0x02 = CCCR 2.00 / SDIO 2.00.  0x00 is the reserved encoding and is
        # what a card must never report (mmc_drv.ko reads this byte last, E0689).
        self.assertEqual(r5 & 0xff, 0x02, f'CCCR revision: 0x{r5:08x}')
        status = self.qtest(self.proc, self.pending, f'readw {MMC1 + SDHC_NORINTSTS:#x}')
        self.assertFalse(status & NIS_ERR, f'enumeration reported an error: 0x{status:04x}')

    def test_cccr_reads_back_written_control_registers(self):
        """The driver writes bus width and int enable, then reads 0x04 back (E0689 trace)."""
        read = lambda addr: 0x00000000 | (addr << 9)
        write = lambda addr, data: 0x80000000 | (addr << 9) | data
        self.command(5)
        r6 = self.command(3)
        self.command(7, arg=r6 & 0xffff0000)
        self.assertEqual(self.command(52, read(0x08)) & 0xff, 0x00)   # capability
        self.assertEqual(self.command(52, read(0x09)) & 0xff, 0x00)   # CIS pointer [0:7]
        self.assertEqual(self.command(52, read(0x0a)) & 0xff, 0x10)   # CIS pointer [8:15]
        self.command(52, write(0x07, 0x02))                            # 4-bit bus
        self.assertEqual(self.command(52, read(0x07)) & 0xff, 0x02)
        self.command(52, write(0x04, 0x01))                            # int enable master
        self.assertEqual(self.command(52, read(0x04)) & 0xff, 0x01)
        status = self.qtest(self.proc, self.pending, f'readw {MMC1 + SDHC_NORINTSTS:#x}')
        self.assertFalse(status & NIS_ERR, f'CCCR access reported an error: 0x{status:04x}')


if __name__ == '__main__':
    unittest.main()
