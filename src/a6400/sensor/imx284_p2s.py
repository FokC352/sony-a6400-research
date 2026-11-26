"""CXD90045 P2S 4-wire sensor-bus decoder and IMX284 mode-table correlator.

Decodes coalesced P2S burst-write frames emitted on CXD90045 P2S channel 1
(``0xFC088340..0xFC08873C``, kick ``0xFC088F08``) into 16-bit IMX284 register
writes, correlates live register state against ``av-cam.bin``'s 13-column /
19-mode static IMX284 configuration table (VA ``0x6454C484`` on ILCE-6400 2.00),
and extracts per-frame exposure, timing, and SLVS-EC streaming state.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import struct

P2S_SYNC_WRITE = 0x81
IMX284_CHIP_ID = 0x81
P2S_MAX_SLOTS = 256
IMX284_REG_SPACE = 0x1000
ORIL_BASE = 0x635C6000

# Mapping from av-cam's 19 logical IMX284 modes (0..18) to the 13 static table
# columns (0..12), verified at VA 0x63F109E1 in ILCE-6400 2.00 av-cam.bin.
IMX284_MODE_TO_COL: tuple[int, ...] = (
    0, 0, 1, 2, 3, 3, 8, 5, 8, 4, 12, 6, 6, 7, 9, 10, 11, 11, 0,
)


@dataclass(frozen=True)
class Imx284ColumnSpec:
    """Verified static fingerprint and readout geometry for one IMX284 column."""

    col: int
    modes: tuple[int, ...]
    active_width: int
    active_height: int
    bit_depth: int
    description: str
    r0000: int
    r0001: int
    r0002: int
    xhs_default: int
    xvs_default: int
    r000f: int
    r0017: int
    r0420: int
    r0421: int
    r0803: int
    r0924: int


IMX284_COLUMNS: tuple[Imx284ColumnSpec, ...] = (
    Imx284ColumnSpec(
        col=0, modes=(0, 1, 18), active_width=6000, active_height=4000,
        bit_depth=14, description="still_full_6000x4000_14bit",
        r0000=0x04, r0001=0x04, r0002=0x00, xhs_default=0x006A, xvs_default=0x046C,
        r000f=0x30, r0017=0x1A, r0420=0x7E, r0421=0x00, r0803=0x80, r0924=0x00,
    ),
    Imx284ColumnSpec(
        col=1, modes=(2,), active_width=6000, active_height=4000,
        bit_depth=12, description="still_cont_hi_6000x4000_12bit",
        r0000=0x0C, r0001=0x04, r0002=0x00, xhs_default=0x006A, xvs_default=0x046C,
        r000f=0x30, r0017=0x1A, r0420=0x02, r0421=0x01, r0803=0x80, r0924=0x01,
    ),
    Imx284ColumnSpec(
        col=2, modes=(3,), active_width=6000, active_height=4000,
        bit_depth=12, description="still_silent_6000x4000_12bit",
        r0000=0x0C, r0001=0x04, r0002=0x00, xhs_default=0x006A, xvs_default=0x046C,
        r000f=0x30, r0017=0x1A, r0420=0x02, r0421=0x01, r0803=0x22, r0924=0x01,
    ),
    Imx284ColumnSpec(
        col=3, modes=(4, 5), active_width=1996, active_height=570,
        bit_depth=11, description="liveview_1996x570_11bit",
        r0000=0x2C, r0001=0x40, r0002=0x10, xhs_default=0x006A, xvs_default=0x046C,
        r000f=0x18, r0017=0x04, r0420=0x99, r0421=0x00, r0803=0x11, r0924=0x30,
    ),
    Imx284ColumnSpec(
        col=4, modes=(9,), active_width=1996, active_height=800,
        bit_depth=11, description="liveview_evf_1996x800_11bit",
        r0000=0x24, r0001=0x40, r0002=0x10, xhs_default=0x006A, xvs_default=0x046C,
        r000f=0x18, r0017=0x06, r0420=0x99, r0421=0x00, r0803=0x11, r0924=0x30,
    ),
    Imx284ColumnSpec(
        col=5, modes=(7,), active_width=1996, active_height=438,
        bit_depth=11, description="draft_af_1996x438_11bit",
        r0000=0x54, r0001=0x00, r0002=0x10, xhs_default=0x006A, xvs_default=0x046C,
        r000f=0x18, r0017=0x02, r0420=0x99, r0421=0x00, r0803=0x11, r0924=0x30,
    ),
    Imx284ColumnSpec(
        col=6, modes=(11, 12), active_width=1996, active_height=1122,
        bit_depth=11, description="movie_fhd_1996x1122_11bit",
        r0000=0x1C, r0001=0x41, r0002=0x10, xhs_default=0x0152, xvs_default=0x0472,
        r000f=0x18, r0017=0x04, r0420=0x99, r0421=0x00, r0803=0x00, r0924=0x30,
    ),
    Imx284ColumnSpec(
        col=7, modes=(13,), active_width=1752, active_height=986,
        bit_depth=11, description="movie_fhd_crop_1752x986_11bit",
        r0000=0x1C, r0001=0x41, r0002=0x10, xhs_default=0x021E, xvs_default=0x03E6,
        r000f=0x10, r0017=0x04, r0420=0x99, r0421=0x00, r0803=0x11, r0924=0x30,
    ),
    Imx284ColumnSpec(
        col=8, modes=(6, 8), active_width=1996, active_height=438,
        bit_depth=11, description="draft_hi_1996x438_11bit",
        r0000=0x54, r0001=0x40, r0002=0x10, xhs_default=0x006A, xvs_default=0x046C,
        r000f=0x18, r0017=0x02, r0420=0x99, r0421=0x00, r0803=0x11, r0924=0x30,
    ),
    Imx284ColumnSpec(
        col=9, modes=(14,), active_width=1024, active_height=684,
        bit_depth=11, description="movie_120p_1024x684_11bit",
        r0000=0x0C, r0001=0x41, r0002=0x00, xhs_default=0x001E, xvs_default=0x02C4,
        r000f=0x18, r0017=0x1A, r0420=0x99, r0421=0x00, r0803=0x00, r0924=0x30,
    ),
    Imx284ColumnSpec(
        col=10, modes=(15,), active_width=4896, active_height=2760,
        bit_depth=11, description="movie_4k30_s35_4896x2760_11bit",
        r0000=0x0C, r0001=0x41, r0002=0x00, xhs_default=0x028A, xvs_default=0x0ADC,
        r000f=0x18, r0017=0x1A, r0420=0x99, r0421=0x00, r0803=0x00, r0924=0x30,
    ),
    Imx284ColumnSpec(
        col=11, modes=(16, 17), active_width=6000, active_height=3376,
        bit_depth=11, description="movie_4k24_6k_6000x3376_11bit",
        r0000=0x0C, r0001=0x41, r0002=0x00, xhs_default=0x0156, xvs_default=0x0D44,
        r000f=0x18, r0017=0x1A, r0420=0x99, r0421=0x00, r0803=0x00, r0924=0x30,
    ),
    Imx284ColumnSpec(
        col=12, modes=(10,), active_width=1996, active_height=1332,
        bit_depth=12, description="liveview_mag_1996x1332_12bit",
        r0000=0x1C, r0001=0x40, r0002=0x10, xhs_default=0x006A, xvs_default=0x046C,
        r000f=0x18, r0017=0x04, r0420=0x7E, r0421=0x00, r0803=0x22, r0924=0x01,
    ),
)

IMX284_COLUMN_BY_ID: dict[int, Imx284ColumnSpec] = {c.col: c for c in IMX284_COLUMNS}


class P2sFrameError(ValueError):
    """Raised when a P2S serial payload violates the CXD90045 burst framing."""


@dataclass(frozen=True)
class P2sBurst:
    """One coalesced register burst inside a P2S serial frame."""

    chip_id: int
    reg_addr: int
    data: bytes

    @property
    def writes(self) -> tuple[tuple[int, int], ...]:
        return tuple((self.reg_addr + idx, b) for idx, b in enumerate(self.data))


@dataclass(frozen=True)
class Imx284ExposureState:
    """Decoded dynamic exposure, timing, and streaming state of the IMX284."""

    standby: bool
    slvs_ec_streaming: bool
    xhs: int
    xvs: int
    shs1: int
    shs2: int
    pgc_gain: int
    wb_clamp_r: int
    dgain_shift: int
    group_hold: int


def decode_p2s_frame(raw: bytes | bytearray | memoryview) -> tuple[P2sBurst, ...]:
    """Decode a CXD90045 P2S byte buffer up to its ``0x00`` frame terminator."""
    buf = bytes(raw)
    if not buf:
        raise P2sFrameError("Empty P2S frame")
    if len(buf) > P2S_MAX_SLOTS:
        raise P2sFrameError(f"P2S frame exceeds {P2S_MAX_SLOTS} slots: {len(buf)}")

    bursts: list[P2sBurst] = []
    pos = 0
    terminated = False
    while pos < len(buf):
        sync = buf[pos]
        if sync == 0x00:
            terminated = True
            break
        if sync != P2S_SYNC_WRITE:
            raise P2sFrameError(f"Unsupported P2S sync byte 0x{sync:02x} at offset {pos}")
        if pos + 2 > len(buf):
            raise P2sFrameError(f"Truncated P2S burst header at offset {pos}")
        length = buf[pos + 1]
        if length < 4:
            raise P2sFrameError(f"Invalid P2S burst length {length} at offset {pos}")
        end = pos + 2 + length
        if end > len(buf):
            raise P2sFrameError(
                f"P2S burst at offset {pos} overruns frame ({end} > {len(buf)})"
            )
        chip_id = buf[pos + 2]
        reg_addr = (buf[pos + 3] << 8) | buf[pos + 4]
        data = buf[pos + 5 : end]
        bursts.append(P2sBurst(chip_id=chip_id, reg_addr=reg_addr, data=data))
        pos = end

    if not terminated:
        raise P2sFrameError("Missing 0x00 terminator in P2S frame")
    return tuple(bursts)


def encode_p2s_frame(bursts: tuple[P2sBurst, ...] | list[P2sBurst]) -> bytes:
    """Serialize P2S bursts into the exact CXD90045 slot byte stream + 0x00."""
    out = bytearray()
    for burst in bursts:
        if not burst.data or len(burst.data) > 250:
            raise P2sFrameError("Invalid P2S burst payload length")
        if not (0 <= burst.reg_addr <= 0xFFFF):
            raise P2sFrameError(f"Out-of-range register address: 0x{burst.reg_addr:x}")
        out.append(P2S_SYNC_WRITE)
        out.append(3 + len(burst.data))
        out.append(burst.chip_id & 0xFF)
        out.append((burst.reg_addr >> 8) & 0xFF)
        out.append(burst.reg_addr & 0xFF)
        out.extend(burst.data)
    out.append(0x00)
    if len(out) > P2S_MAX_SLOTS:
        raise P2sFrameError(f"Encoded P2S frame exceeds {P2S_MAX_SLOTS} slots: {len(out)}")
    return bytes(out)


class Imx284VirtualSensor:
    """Stateful IMX284 4-wire register file and mode-table recognizer."""

    def __init__(self) -> None:
        self.regs = bytearray(IMX284_REG_SPACE)
        self.valid = bytearray(IMX284_REG_SPACE)
        self.frames_applied = 0
        self.writes_applied = 0

    def reset(self) -> None:
        self.regs[:] = b"\x00" * IMX284_REG_SPACE
        self.valid[:] = b"\x00" * IMX284_REG_SPACE
        self.frames_applied = 0
        self.writes_applied = 0

    def read_u8(self, reg: int) -> int | None:
        if not (0 <= reg < IMX284_REG_SPACE) or not self.valid[reg]:
            return None
        return self.regs[reg]

    def read_u16_le(self, reg: int) -> int | None:
        lo = self.read_u8(reg)
        hi = self.read_u8(reg + 1)
        if lo is None or hi is None:
            return None
        return lo | (hi << 8)

    def apply_frame(self, raw: bytes | bytearray | memoryview) -> tuple[P2sBurst, ...]:
        bursts = decode_p2s_frame(raw)
        for burst in bursts:
            if burst.chip_id != IMX284_CHIP_ID:
                continue
            for offset, val in enumerate(burst.data):
                reg = burst.reg_addr + offset
                if 0 <= reg < IMX284_REG_SPACE:
                    self.regs[reg] = val
                    self.valid[reg] = 1
                    self.writes_applied += 1
        self.frames_applied += 1
        return bursts

    def recognize_column(self) -> Imx284ColumnSpec | None:
        """Match programmed registers against the 13 static IMX284 mode columns."""
        required = (0x0000, 0x0001, 0x0002, 0x000F, 0x0017, 0x0803)
        if any(not self.valid[r] for r in required):
            return None

        r00 = self.regs[0x0000]
        r01 = self.regs[0x0001]
        r02 = self.regs[0x0002]
        r0f = self.regs[0x000F]
        r17 = self.regs[0x0017]
        r803 = self.regs[0x0803]
        r924 = self.read_u8(0x0924)
        r420 = self.read_u8(0x0420)
        xhs = self.read_u16_le(0x000A)
        xvs = self.read_u16_le(0x000C)

        matches: list[Imx284ColumnSpec] = []
        for spec in IMX284_COLUMNS:
            if (
                spec.r0000 != r00
                or spec.r0001 != r01
                or spec.r0002 != r02
                or spec.r000f != r0f
                or spec.r0017 != r17
                or spec.r0803 != r803
            ):
                continue
            if r924 is not None and spec.r0924 != r924:
                continue
            # Column 0 (full still) patches 0x0420 dynamically in 1H2H helper,
            # while columns 3 vs 12 and 9/10/11 distinguish via r0420 or XHS/XVS.
            if spec.col in (3, 12) and r420 is not None and spec.r0420 != r420:
                continue
            if spec.col in (9, 10, 11) and xhs is not None and spec.xhs_default != xhs:
                continue
            if spec.col in (9, 10, 11) and xvs is not None and spec.xvs_default != xvs:
                continue
            matches.append(spec)

        return matches[0] if len(matches) == 1 else (matches[0] if matches else None)

    def candidate_modes(self) -> tuple[int, ...]:
        spec = self.recognize_column()
        return spec.modes if spec is not None else ()

    def exposure_state(self) -> Imx284ExposureState:
        r00 = self.regs[0x0000] if self.valid[0x0000] else 0x01
        rb3 = self.regs[0x00B3] if self.valid[0x00B3] else 0x00
        return Imx284ExposureState(
            standby=bool(r00 & 0x01) or not self.valid[0x0000],
            slvs_ec_streaming=(rb3 == 0x90),
            xhs=self.read_u16_le(0x000A) or 0,
            xvs=self.read_u16_le(0x000C) or 0,
            shs1=(self.read_u16_le(0x0003) or 0) & 0x0FFF,
            shs2=(self.read_u16_le(0x0005) or 0) & 0x0FFF,
            pgc_gain=(self.read_u16_le(0x0007) or 0) & 0x3FFF,
            wb_clamp_r=self.read_u16_le(0x001D) or 0,
            dgain_shift=self.regs[0x003F] if self.valid[0x003F] else 0,
            group_hold=self.regs[0x099C] if self.valid[0x099C] else 0,
        )


_SENSOR_FRAME_RE = re.compile(
    r"\[sensor frame\]\s+cpu=(\d+)\s+seq=(\d+)\s+channel=(\d+)\s+bytes=(\d+)\s+hex=([0-9a-fA-F]+)"
)


@dataclass(frozen=True)
class TracedP2sFrame:
    """One captured ``[sensor frame]`` line from a QEMU P2S trace log."""

    cpu: int
    seq: int
    channel: int
    payload: bytes


def parse_sensor_frame_log(log_text: str) -> tuple[TracedP2sFrame, ...]:
    """Extract all ``[sensor frame]`` entries from a QEMU console/trace log."""
    frames: list[TracedP2sFrame] = []
    for match in _SENSOR_FRAME_RE.finditer(log_text):
        cpu, seq, ch, nbytes, hex_str = match.groups()
        raw = bytes.fromhex(hex_str)
        if len(raw) != int(nbytes):
            raise P2sFrameError(
                f"Frame seq={seq} declared {nbytes} bytes, got {len(raw)}"
            )
        frames.append(
            TracedP2sFrame(cpu=int(cpu), seq=int(seq), channel=int(ch), payload=raw)
        )
    return tuple(frames)


def extract_avcam_imx284_mode_table(avcam_bytes: bytes) -> dict[int, dict[int, int]]:
    """Locate and extract the 125-row x 13-column IMX284 register table in av-cam.bin.

    Each 20-byte row has ``(u32 desc_ptr, u8 values[13], u8 pad[3])``, where
    ``desc_ptr - 2`` holds the 16-bit ``reg_addr`` inside ``av-cam.bin``'s
    register-descriptor pool. Returns ``{col_index: {reg_addr: byte_val}}``.
    """
    row0_values = bytes(
        [0x04, 0x0C, 0x0C, 0x2C, 0x24, 0x54, 0x1C, 0x1C, 0x54, 0x0C, 0x0C, 0x0C, 0x1C]
    )
    val_off = avcam_bytes.find(row0_values)
    if val_off < 4:
        raise ValueError("IMX284 13-column mode table anchor not found in av-cam.bin")
    pos = val_off - 4

    columns: dict[int, dict[int, int]] = {col: {} for col in range(13)}
    for row in range(125):
        off = pos + row * 20
        if off + 20 > len(avcam_bytes):
            break
        desc_va = struct.unpack_from("<I", avcam_bytes, off)[0]
        reg_off = desc_va - ORIL_BASE - 2
        if not (0 <= reg_off <= len(avcam_bytes) - 2):
            break
        reg_addr = struct.unpack_from("<H", avcam_bytes, reg_off)[0]
        if reg_addr >= IMX284_REG_SPACE:
            break
        vals = avcam_bytes[off + 4 : off + 17]
        for col in range(13):
            # Later rows override earlier duplicate defaults (e.g. row 56 vs 99 for 0x0803)
            columns[col][reg_addr] = vals[col]

    if len(columns[0]) < 100:
        raise ValueError(f"Incomplete IMX284 mode table at 0x{pos + ORIL_BASE:08x}")
    return columns
