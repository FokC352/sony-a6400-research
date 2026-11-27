"""QEMU cxd90045-dwc3 unix-socket adapter for camera_debugger and PMCA extcmd."""
import contextlib
import socket
import struct
import time
from pathlib import Path

from tools.research.qemu_ep0_host import Ep0Host
from tools.research.qemu_usb_connections import (
    MSC_PIDS,
    MscBotClient,
    MscCommandFailedError,
    PTP_PIDS,
    PTP_RC_OK,
    PcRemotePtpClient,
    PtpResponseError,
    SONY_VID,
)

FRAME_SIZE = 4096
FEATURE_DEBUGGER = 0x23
PTP_OC_SONY_DI_EXTCMD_WRITE = 0x9280
PTP_OC_SONY_DI_EXTCMD_READ = 0x9281
PTP_OC_SONY_REQ_RECONNECT = 0x9282
PTP_RC_DEVICE_BUSY = 0x2019
SCSI_SENSE_NOT_READY = 0x02


class QemuDwc3MscExchange:
    """4096-byte camera_debugger exchange over QEMU DWC3 Mass Storage (CDB 0x7A)."""

    def __init__(self, msc_client: MscBotClient, *, feature: int = FEATURE_DEBUGGER,
                 lun: int = 0, timeout: float = 10.0):
        self.msc = msc_client
        self.feature = feature
        self.lun = lun
        self.timeout = timeout
        self._cdb = b'\x7a' + struct.pack('<I', self.feature) + b'\x00' * 7

    def __call__(self, payload: bytes) -> bytes:
        if len(payload) != FRAME_SIZE:
            raise ValueError(f'Expected {FRAME_SIZE}-byte debugger frame, got {len(payload)}')
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                res = self.msc.scsi_command(
                    self._cdb, data_out=payload, lun=self.lun
                )
            except MscCommandFailedError:
                res = {'status': 1}
            if res['status'] == 0:
                break
            sense = self.msc.request_sense(lun=self.lun)
            if sense['sense_key'] == SCSI_SENSE_NOT_READY and time.monotonic() < deadline:
                time.sleep(0.02)
                continue
            raise RuntimeError(
                f'MSC debugger write failed (sense=0x{sense["sense_key"]:02x}/'
                f'0x{sense["asc"]:02x}/0x{sense["ascq"]:02x})'
            )

        while True:
            res = self.msc.scsi_command(
                self._cdb, data_in_len=FRAME_SIZE, lun=self.lun
            )
            if res['status'] == 0:
                data = res['data']
                if len(data) != FRAME_SIZE:
                    raise RuntimeError(
                        f'Expected {FRAME_SIZE}-byte debugger reply, got {len(data)}'
                    )
                return data
            sense = self.msc.request_sense(lun=self.lun)
            if sense['sense_key'] == SCSI_SENSE_NOT_READY and time.monotonic() < deadline:
                time.sleep(0.02)
                continue
            raise RuntimeError(
                f'MSC debugger read failed (sense=0x{sense["sense_key"]:02x}/'
                f'0x{sense["asc"]:02x}/0x{sense["ascq"]:02x})'
            )

    def close(self) -> None:
        pass


class QemuDwc3PtpExchange:
    """4096-byte camera_debugger exchange over QEMU DWC3 PTP (0x9280 / 0x9281)."""

    def __init__(self, ptp_client: PcRemotePtpClient, *, feature: int = FEATURE_DEBUGGER,
                 timeout: float = 10.0):
        self.ptp = ptp_client
        self.feature = feature
        self.timeout = timeout

    def _ensure_session(self) -> None:
        if not self.ptp.session_open:
            self.ptp.open_session(1)

    def __call__(self, payload: bytes) -> bytes:
        if len(payload) != FRAME_SIZE:
            raise ValueError(f'Expected {FRAME_SIZE}-byte debugger frame, got {len(payload)}')
        self._ensure_session()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.ptp.transact(
                    PTP_OC_SONY_DI_EXTCMD_WRITE,
                    (self.feature,),
                    data_out=payload,
                )
                break
            except (PtpResponseError, ValueError) as exc:
                is_busy = (
                    isinstance(exc, PtpResponseError)
                    and exc.response_code == PTP_RC_DEVICE_BUSY
                ) or (
                    isinstance(exc, ValueError)
                    and 'kind=3' in str(exc)
                    and f'0x{PTP_RC_DEVICE_BUSY:04x}' in str(exc).lower()
                )
                if is_busy and time.monotonic() < deadline:
                    time.sleep(0.02)
                    continue
                raise

        while True:
            try:
                res = self.ptp.transact(
                    PTP_OC_SONY_DI_EXTCMD_READ,
                    (self.feature,),
                    expect_data=True,
                )
                data = res['data']
                if len(data) != FRAME_SIZE:
                    raise RuntimeError(
                        f'Expected {FRAME_SIZE}-byte debugger reply, got {len(data)}'
                    )
                return data
            except (PtpResponseError, ValueError) as exc:
                is_busy = (
                    isinstance(exc, PtpResponseError)
                    and exc.response_code == PTP_RC_DEVICE_BUSY
                ) or (
                    isinstance(exc, ValueError)
                    and 'kind=3' in str(exc)
                    and f'0x{PTP_RC_DEVICE_BUSY:04x}' in str(exc).lower()
                )
                if is_busy and time.monotonic() < deadline:
                    time.sleep(0.02)
                    continue
                raise

    def switch_to_msc(self) -> dict:
        """Send Sony PTP 0x9282 (SonyReqReconnect arg 0) to request MSC mode."""
        self._ensure_session()
        res = self.ptp.transact(PTP_OC_SONY_REQ_RECONNECT, (0,))
        return {'response_code': res['response_code'], 'transaction_id': res['transaction_id']}

    def close(self) -> None:
        if self.ptp.session_open:
            try:
                self.ptp.close_session()
            except Exception:
                pass


@contextlib.contextmanager
def open_qemu_exchange(socket_path: str | Path, *, mode: str = 'auto',
                       timeout: float = 15.0, connect_bus: bool = True):
    """Connect to a QEMU cxd90045-dwc3 unix socket and yield (exchange, details)."""
    if mode not in ('auto', 'msc', 'ptp'):
        raise ValueError(f'Unsupported QEMU debugger mode: {mode!r}')
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    sock.connect(str(socket_path))
    try:
        host = Ep0Host(sock, deadline=time.monotonic() + timeout)
        if connect_bus:
            host.connect()
            host.bus_reset()
        enum = host.enumerate_device()
        dev = bytes.fromhex(enum['device_hex'])
        vid, pid = struct.unpack_from('<HH', dev, 8)
        if vid != SONY_VID:
            raise RuntimeError(f'Unexpected USB VID:PID {vid:04x}:{pid:04x}')

        selected_mode = mode
        if selected_mode == 'auto':
            if pid in MSC_PIDS:
                selected_mode = 'msc'
            elif pid in PTP_PIDS or pid == 0x0322:
                selected_mode = 'ptp'
            else:
                raise RuntimeError(f'Unsupported Sony USB PID 0x{pid:04x} for debugger')

        if selected_mode == 'msc':
            client = MscBotClient(host, enum)
            exchange = QemuDwc3MscExchange(client, timeout=timeout)
        else:
            allowed = PTP_PIDS if pid in PTP_PIDS else (pid,)
            client = PcRemotePtpClient(host, enum, allowed_pids=allowed)
            exchange = QemuDwc3PtpExchange(client, timeout=timeout)

        details = {
            'transport': 'qemu',
            'mode': selected_mode,
            'vid': vid,
            'pid': pid,
            'socket': str(socket_path),
            'configuration_value': enum['configuration_value'],
        }
        try:
            yield exchange, details
        finally:
            exchange.close()
    finally:
        sock.close()
