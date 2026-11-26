"""Sensor configuration table mapping and bounded emulation for BIONZ X (CXD90045).

Covers:
* ``sabin/`` Signal Accelerator (SA), DCV PSF, Spica/Musashi tap-filter, and IDT/SSBOOT binaries
* ``dfe_app.bin`` Data Front-End (DFE) XDMAC/padding helper linked at ``0xFE1ED000``
* ``av-cam.bin`` ORIL-mapped ``Camera::Imager::IMX284`` mode/geometry and MMIO register tables
"""
from .dfe_app import DfeAppInfo, DfeAppModel, DfeError, parse_dfe_app
from .imager_tables import (
    ImagerMmioBank,
    ImagerTableError,
    ImagerTableSummary,
    MmioWrite,
    SensorGeometry,
    analyze_avcam_imager,
)
from .imx284_p2s import (
    IMX284_CHIP_ID,
    IMX284_COLUMNS,
    IMX284_MODE_TO_COL,
    Imx284ColumnSpec,
    Imx284ExposureState,
    Imx284VirtualSensor,
    P2sBurst,
    P2sFrameError,
    TracedP2sFrame,
    decode_p2s_frame,
    encode_p2s_frame,
    extract_avcam_imx284_mode_table,
    parse_sensor_frame_log,
)
from .sabin import (
    DcvPsfHeader,
    IdtSsbootHeader,
    SaModuleHeader,
    SabinInventory,
    SpicaMusashiEntry,
    parse_dcv_psf,
    parse_idt_ssboot,
    parse_sa_module,
    scan_sabin_tree,
)

__all__ = [
    "DcvPsfHeader",
    "DfeAppInfo",
    "DfeAppModel",
    "DfeError",
    "IdtSsbootHeader",
    "ImagerMmioBank",
    "ImagerTableError",
    "ImagerTableSummary",
    "IMX284_CHIP_ID",
    "IMX284_COLUMNS",
    "IMX284_MODE_TO_COL",
    "Imx284ColumnSpec",
    "Imx284ExposureState",
    "Imx284VirtualSensor",
    "MmioWrite",
    "P2sBurst",
    "P2sFrameError",
    "SaModuleHeader",
    "SabinInventory",
    "SensorGeometry",
    "SpicaMusashiEntry",
    "TracedP2sFrame",
    "analyze_avcam_imager",
    "decode_p2s_frame",
    "encode_p2s_frame",
    "extract_avcam_imx284_mode_table",
    "parse_dcv_psf",
    "parse_dfe_app",
    "parse_idt_ssboot",
    "parse_sa_module",
    "parse_sensor_frame_log",
    "scan_sabin_tree",
]
