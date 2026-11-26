"""Use a declared raw SD image and verify offline playback or JPEG output."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import struct
import time
from typing import TYPE_CHECKING

from src.a6400.execution.guest_vfat import VfatImage
from tools.research.inventory import digest

if TYPE_CHECKING:
    from tools.research.qemu_boot_probe import Qmp


SD_DRIVE = 'a6400-sd'
SD_NODE = 'a6400-sd-image'


def sd_blockdev(image: Path, sha256: str, inputs: Path, run_dir: Path) -> dict:
    if not re.fullmatch(r'[0-9a-f]{64}', sha256):
        raise ValueError('SD_SHA256 must identify the raw card contents (64 lowercase hex digits)')
    image = image.resolve(strict=True)
    info = image.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('The SD medium must be a regular, non-hardlinked image file')
    if image.is_relative_to(inputs.resolve()) or image.is_relative_to(run_dir.resolve()):
        raise ValueError('The SD medium must be outside firmware inputs and the disposable run directory')
    size = info.st_size
    if size < 2**20 or (size <= 2**31 and size & (size - 1)) or size % (512 * 1024):
        raise ValueError('The SD image needs power-of-two SDSC or 512 KiB-aligned SDHC capacity')
    if digest(image) != sha256:
        raise ValueError('SD image SHA-256 mismatch')
    return {'driver': 'raw', 'node-name': SD_NODE,
            'file': {'driver': 'file', 'filename': str(image)}}


def sd_event(qmp: 'Qmp', action: str) -> None:
    if action not in ('insert', 'eject'):
        raise ValueError(f'Unknown SD operation: {action}')
    drives = [drive for drive in qmp.request('query-block') if drive['device'] == SD_DRIVE]
    if len(drives) != 1 or not drives[0].get('qdev') or not drives[0].get('removable'):
        raise ValueError('No attached a6400 removable SD slot')
    drive = drives[0]
    device = drive['qdev']
    if action == 'insert':
        qmp.request('blockdev-insert-medium', {'id': device, 'node-name': SD_NODE})
    else:
        if drive.get('inserted', {}).get('node-name') != SD_NODE:
            raise ValueError('The predeclared SD image is not inserted')
        qmp.request('blockdev-remove-medium', {'id': device})


def verify_photo(before: Path, after: Path, *, reopened: Path | None = None) -> dict:
    """Require one new, independently decodable JPEG in an exported DCIM tree."""
    from PIL import Image

    def photos(root: Path) -> dict[str, Path]:
        dcim = root / 'DCIM'
        if not root.is_dir():
            raise ValueError(f'Card export directory does not exist: {root}')
        return {p.relative_to(dcim).as_posix().upper(): p for p in dcim.glob('*/*')
                if p.is_file() and p.suffix.lower() in ('.jpg', '.jpeg')}

    existing, current = photos(before), photos(after)
    added = current.keys() - existing.keys()
    if len(added) != 1:
        raise ValueError(f'Expected exactly one new DCIM JPEG, found {len(added)}')
    name = added.pop()
    photo = current[name]
    sha256 = digest(photo)
    if any(digest(path) == sha256 for path in existing.values()):
        raise ValueError('New filename contains an unchanged baseline JPEG')
    with Image.open(photo, formats=('JPEG',)) as image:
        image.load()
        width, height = image.size
    result = {'path': f'DCIM/{name}', 'sha256': sha256, 'width': width, 'height': height,
              'verification': 'new decodable JPEG; provenance depends on the replay controls'}
    if reopened is not None:
        if reopened.resolve() in (before.resolve(), after.resolve()):
            raise ValueError('Provide a separate DCIM export from the reopened card')
        persisted = photos(reopened).get(name)
        if persisted is None:
            raise ValueError(f'Reopened card has no DCIM/{name}')
        reopened_sha256 = digest(persisted)
        if reopened_sha256 != sha256:
            raise ValueError(f'Reopened DCIM/{name} does not match the saved JPEG')
        result['reopened_sha256'] = reopened_sha256
    return result


def verify_playback(fixture: Path, empty: Path, inserted: Path,
                    image_box: tuple[int, int, int, int] | None = None) -> dict:
    """Compare QEMU RGB captures against a high-contrast playback fixture."""
    from PIL import Image, ImageChops, ImageStat

    sample_size = (64, 48)
    with Image.open(fixture, formats=('JPEG',)) as photo:
        expected = photo.convert('RGB').resize(sample_size, Image.Resampling.BOX)
    scores = []
    head_size = None
    for path in (empty, inserted):
        with Image.open(path, formats=('PPM',)) as display:
            if head_size is not None and display.size != head_size:
                raise ValueError('Compare the same QEMU head geometry in both captures')
            head_size = display.size
            box = image_box or (0, 0, *head_size)
            left, top, right, bottom = box
            if not (0 <= left < right <= head_size[0] and 0 <= top < bottom <= head_size[1]):
                raise ValueError('Playback image box must fit inside the QEMU head')
            actual = display.convert('RGB').crop(box).resize(sample_size, Image.Resampling.BOX)
            scores.append(max(ImageStat.Stat(ImageChops.difference(expected, actual)).mean))
    empty_error, inserted_error = scores
    threshold = 12
    detail = f'empty error {empty_error:.2f}; inserted error {inserted_error:.2f}; limit {threshold}'
    if empty_error <= threshold:
        raise ValueError(f'Empty-card control already matches the fixture ({detail})')
    if inserted_error > threshold:
        raise ValueError(f'Inserted-card capture does not show the fixture ({detail})')
    return {'fixture_sha256': digest(fixture), 'empty_head_sha256': digest(empty),
            'inserted_head_sha256': digest(inserted), 'image_box': box,
            'worst_channel_mae': {'empty': empty_error, 'inserted': inserted_error},
            'mae_limit': threshold}


SDSC_MAX_BYTES = 2 * 1024 ** 3
SDHC_MAX_BYTES = 32 * 1024 ** 3
SDHC_ALIGN_BYTES = 512 * 1024


def validate_uhs_sdhc_size(size_bytes: int) -> None:
    """Enforce SDHC (>2 GiB, <=32 GiB, 512 KiB-aligned) geometry for UHS-I / XAVC S 4K."""
    if not (SDSC_MAX_BYTES < size_bytes <= SDHC_MAX_BYTES):
        raise ValueError(
            f'UHS-I SDHC media requires >2 GiB and <=32 GiB capacity, got {size_bytes} bytes'
        )
    if size_bytes % SDHC_ALIGN_BYTES != 0:
        raise ValueError(
            f'SDHC capacity must be a multiple of 512 KiB, got {size_bytes} bytes'
        )


def create_sdhc_fat32_image(
    path: Path,
    *,
    size_gib: int = 4,
    cluster_kib: int = 32,
    volume_label: str = 'SONY_SDHC',
    files: dict[str, bytes] | None = None,
) -> dict:
    """Create a deterministic sparse MBR + FAT32 SDHC image (>2 GiB) for UHS-I / XAVC S."""
    size_bytes = int(size_gib) * (1024 ** 3)
    validate_uhs_sdhc_size(size_bytes)
    if cluster_kib not in (16, 32, 64):
        raise ValueError(f'Unsupported SDHC FAT32 cluster size: {cluster_kib} KiB')
    label = volume_label.encode('ascii', errors='strict').ljust(11, b' ')[:11]

    sector_size = 512
    total_sectors = size_bytes // sector_size
    part_lba = 8192  # 4 MiB boundary matching UHS AU_SIZE
    part_sectors = total_sectors - part_lba
    spc = (cluster_kib * 1024) // sector_size
    reserved_sectors = 32
    fats = 2

    # Compute FAT32 table size in sectors with two fixed-point iterations
    fat_sectors = ((part_sectors - reserved_sectors) // spc * 4 + sector_size - 1) // sector_size
    data_sectors = part_sectors - reserved_sectors - fats * fat_sectors
    clusters = data_sectors // spc
    fat_sectors = ((clusters + 2) * 4 + sector_size - 1) // sector_size
    # Pad FAT size to cluster alignment
    fat_sectors = ((fat_sectors + spc - 1) // spc) * spc
    data_sectors = part_sectors - reserved_sectors - fats * fat_sectors
    clusters = data_sectors // spc
    if clusters < 65525:
        raise ValueError(f'Partition too small for FAT32 ({clusters} clusters)')

    mbr = bytearray(sector_size)
    # Partition 1 entry at 0x1BE: status=0x00, type=0x0C (FAT32 LBA)
    struct.pack_into('<BBBBBBBBII', mbr, 0x1BE,
                     0x00, 0xFE, 0xFF, 0xFF, 0x0C, 0xFE, 0xFF, 0xFF,
                     part_lba, part_sectors)
    mbr[0x1FE:0x200] = b'\x55\xAA'

    vbr = bytearray(sector_size)
    vbr[0:3] = b'\xEB\x58\x90'
    vbr[3:11] = b'MSWIN4.1'
    struct.pack_into('<HBHBHHBHHHII', vbr, 0x0B,
                     sector_size, spc, reserved_sectors, fats,
                     0, 0, 0xF8, 0, 63, 255, part_lba, part_sectors)
    struct.pack_into('<IHHIHH', vbr, 0x24,
                     fat_sectors, 0, 0, 2, 1, 6)
    vbr[0x40] = 0x80
    vbr[0x42] = 0x29
    struct.pack_into('<I', vbr, 0x43, 0xA6400003)
    vbr[0x47:0x52] = label
    vbr[0x52:0x5A] = b'FAT32   '
    vbr[0x1FE:0x200] = b'\x55\xAA'

    fsinfo = bytearray(sector_size)
    fsinfo[0:4] = b'RRaA'
    fsinfo[0x1E4:0x1E8] = b'rrAa'
    struct.pack_into('<II', fsinfo, 0x1E8, clusters - 1, 3)
    fsinfo[0x1FE:0x200] = b'\x55\xAA'

    fat_head = bytearray(sector_size)
    struct.pack_into('<III', fat_head, 0, 0x0FFFFFF8, 0x0FFFFFFF, 0x0FFFFFFF)

    cluster_bytes = spc * sector_size
    extra_clusters: dict[int, bytes] = {}
    next_cluster = 3
    root_cluster = bytearray(cluster_bytes)
    root_cluster[0:11] = label
    root_cluster[11] = 0x08  # ATTR_VOLUME_ID

    def _dir_entry(name11: bytes, attr: int, clus: int, size: int = 0) -> bytes:
        ent = bytearray(32)
        ent[0:11] = name11
        ent[11] = attr
        struct.pack_into('<H', ent, 20, (clus >> 16) & 0xFFFF)
        struct.pack_into('<H', ent, 26, clus & 0xFFFF)
        struct.pack_into('<I', ent, 28, size)
        return bytes(ent)

    def _short83(seg: str) -> bytes:
        stem, _, ext = seg.upper().partition('.')
        return stem[:8].ljust(8).encode('ascii') + ext[:3].ljust(3).encode('ascii')

    if files:
        # Build single-cluster directories + contiguous file cluster chains
        dir_clusters: dict[tuple[str, ...], int] = {(): 2}
        dir_buffers: dict[int, bytearray] = {2: root_cluster}
        dir_slots: dict[int, int] = {2: 32}

        def _ensure_fat_cap(c: int) -> None:
            need = (c + 1) * 4
            if need > len(fat_head):
                fat_head.extend(b'\x00' * (((need - len(fat_head) + sector_size - 1) // sector_size) * sector_size))

        for rel_path, payload in sorted(files.items()):
            parts = [p for p in rel_path.strip('/').split('/') if p]
            parent_key: tuple[str, ...] = ()
            for depth in range(len(parts) - 1):
                cur_key = tuple(p.upper() for p in parts[: depth + 1])
                if cur_key not in dir_clusters:
                    d_clus = next_cluster
                    next_cluster += 1
                    _ensure_fat_cap(d_clus)
                    struct.pack_into('<I', fat_head, d_clus * 4, 0x0FFFFFFF)
                    p_clus = dir_clusters[parent_key]
                    p_buf = dir_buffers[p_clus]
                    off = dir_slots[p_clus]
                    p_buf[off : off + 32] = _dir_entry(_short83(parts[depth]), 0x10, d_clus, 0)
                    dir_slots[p_clus] = off + 32
                    d_buf = bytearray(cluster_bytes)
                    d_buf[0:32] = _dir_entry(b'.          ', 0x10, d_clus, 0)
                    d_buf[32:64] = _dir_entry(b'..         ', 0x10, 0 if p_clus == 2 else p_clus, 0)
                    dir_clusters[cur_key] = d_clus
                    dir_buffers[d_clus] = d_buf
                    dir_slots[d_clus] = 64
                    extra_clusters[d_clus] = d_buf
                parent_key = cur_key

            n_clus = max(1, (len(payload) + cluster_bytes - 1) // cluster_bytes)
            first_c = next_cluster
            for idx_c in range(n_clus):
                c = first_c + idx_c
                _ensure_fat_cap(c)
                nxt = 0x0FFFFFFF if idx_c == n_clus - 1 else (c + 1)
                struct.pack_into('<I', fat_head, c * 4, nxt)
                chunk = payload[idx_c * cluster_bytes : (idx_c + 1) * cluster_bytes]
                extra_clusters[c] = chunk.ljust(cluster_bytes, b'\x00')
            next_cluster += n_clus
            p_clus = dir_clusters[parent_key]
            p_buf = dir_buffers[p_clus]
            off = dir_slots[p_clus]
            p_buf[off : off + 32] = _dir_entry(_short83(parts[-1]), 0x20, first_c, len(payload))
            dir_slots[p_clus] = off + 32

    struct.pack_into('<II', fsinfo, 0x1E8, max(0, clusters + 2 - next_cluster), next_cluster)

    path = path.resolve()
    with path.open('xb') as f:
        f.truncate(size_bytes)
        f.seek(0)
        f.write(mbr)
        part_off = part_lba * sector_size
        f.seek(part_off)
        f.write(vbr)
        f.seek(part_off + sector_size)
        f.write(fsinfo)
        f.seek(part_off + 6 * sector_size)
        f.write(vbr)
        f.seek(part_off + 7 * sector_size)
        f.write(fsinfo)
        for idx in range(fats):
            fat_off = part_off + (reserved_sectors + idx * fat_sectors) * sector_size
            f.seek(fat_off)
            f.write(fat_head)
        data_off = part_off + (reserved_sectors + fats * fat_sectors) * sector_size
        f.seek(data_off)
        f.write(root_cluster)
        for c, c_bytes in sorted(extra_clusters.items()):
            f.seek(data_off + (c - 2) * cluster_bytes)
            f.write(c_bytes)

    return {
        'path': str(path),
        'bytes': size_bytes,
        'partition_lba': part_lba,
        'partition_sectors': part_sectors,
        'cluster_bytes': spc * sector_size,
        'clusters': clusters,
    }


def build_sony_exif_app1(
    width: int,
    height: int,
    model: str = 'ILCE-6400',
    thumbnail_jpeg: bytes | None = None,
) -> bytes:
    """Build a minimal TIFF/Exif payload (with optional IFD1 thumbnail) for libmpr & dgis_jpeg."""
    make_b = b'SONY\x00\x00'
    model_b = model.encode('ascii') + b'\x00'
    if len(model_b) % 2:
        model_b += b'\x00'
    # IFD0 starts at offset 8: 2B count + 4*12B entries + 4B next = 54 bytes -> offset 62
    make_off = 62
    model_off = make_off + len(make_b)
    exif_ifd_off = model_off + len(model_b)
    # Exif SubIFD: 2B count + 4*12B entries + 4B next = 54 bytes
    ifd1_off = (exif_ifd_off + 54) if thumbnail_jpeg else 0
    tiff = bytearray()
    tiff += b'II' + struct.pack('<HI', 42, 8)
    tiff += struct.pack('<H', 4)
    tiff += struct.pack('<HHII', 0x010F, 2, 5, make_off)
    tiff += struct.pack('<HHII', 0x0110, 2, len(model) + 1, model_off)
    tiff += struct.pack('<HHHI', 0x0112, 3, 1, 1)[:8] + struct.pack('<HH', 1, 0)
    tiff += struct.pack('<HHII', 0x8769, 4, 1, exif_ifd_off)
    tiff += struct.pack('<I', ifd1_off)
    tiff += make_b + model_b
    tiff += struct.pack('<H', 4)
    tiff += struct.pack('<HHII', 0x9000, 7, 4, int.from_bytes(b'0230', 'little'))
    tiff += struct.pack('<HHHHH', 0xA001, 3, 1, 0, 1)[:8] + struct.pack('<HH', 1, 0)
    tiff += struct.pack('<HHII', 0xA002, 4, 1, int(width))
    tiff += struct.pack('<HHII', 0xA003, 4, 1, int(height))
    tiff += struct.pack('<I', 0)
    if thumbnail_jpeg:
        # IFD1: Compression=6 (JPEG), JPEGInterchangeFormat, JPEGInterchangeFormatLength
        thumb_off = ifd1_off + 2 + 3 * 12 + 4
        tiff += struct.pack('<H', 3)
        tiff += struct.pack('<HHHH', 0x0103, 3, 1, 0)[:6] + struct.pack('<IH', 1, 6)[:6]
        # Wait: TIFF entry is tag(H), type(H), count(I), value_or_offset(I):
        del tiff[-12:]
        tiff += struct.pack('<HHIHH', 0x0103, 3, 1, 6, 0)
        tiff += struct.pack('<HHII', 0x0201, 4, 1, thumb_off)
        tiff += struct.pack('<HHII', 0x0202, 4, 1, len(thumbnail_jpeg))
        tiff += struct.pack('<I', 0)
        tiff += thumbnail_jpeg
    return b'Exif\x00\x00' + bytes(tiff)


def encode_firmware_compliant_jpeg(image, *, quality: int = 92, model: str = 'ILCE-6400') -> bytes:
    """Encode a PIL Image as baseline YCbCr 4:2:2 with Annex K DHT & Sony Exif + 160x120 thumb."""
    import io

    rgb = image.convert('RGB')
    width, height = rgb.size
    thumb_buf = io.BytesIO()
    rgb.resize((160, 120)).save(
        thumb_buf,
        format='JPEG',
        quality=85,
        subsampling=1,
        optimize=False,
    )
    buf = io.BytesIO()
    rgb.save(
        buf,
        format='JPEG',
        quality=quality,
        subsampling=1,   # SOF0 H1V1 = 0x21 (4:2:2); 0x11 (4:4:4) fails with 0xFB030005
        optimize=False,  # preserves standard ITU-T T.81 Annex K DHT (0x63a08950)
        exif=build_sony_exif_app1(width, height, model=model, thumbnail_jpeg=thumb_buf.getvalue()),
    )
    return buf.getvalue()


def extract_setting_backup(setting_part_bytes: bytes) -> bytes:
    """Extract and validate /Backup.bin from the 20 MiB FAT12 /setting partition."""
    image = VfatImage(setting_part_bytes, expected_size=len(setting_part_bytes))
    matches = [
        entry for entry in image.list_dir()
        if not entry.is_dir and entry.display_name.casefold() == 'backup.bin'
    ]
    if len(matches) != 1:
        raise ValueError(f'Expected one Backup.bin in /setting, found {len(matches)}')
    data = image.read_file(matches[0])
    if len(data) < 16 or data[4:8] != b'\xaa\xaa\xaa\xaa' or data[12:16] != b'BK4\x00':
        raise ValueError('Extracted Backup.bin failed BK4 header validation')
    load_pa = struct.unpack_from('<I', data, 0)[0]
    if load_pa % 4 != 0 or load_pa == 0:
        raise ValueError(f'Invalid Backup.bin preload PA: 0x{load_pa:08x}')
    return data


def extract_setting_backup_file(part_image: Path, output: Path) -> dict:
    raw = part_image.read_bytes()
    backup = extract_setting_backup(raw)
    tmp = output.with_suffix(output.suffix + '.tmp')
    tmp.write_bytes(backup)
    tmp.replace(output)
    return {
        'output': str(output),
        'bytes': len(backup),
        'sha256': hashlib.sha256(backup).hexdigest(),
        'load_pa': struct.unpack_from('<I', backup, 0)[0],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    sd = commands.add_parser('sd-blockdev', help='Validate a raw card and print its QEMU block node')
    sd.add_argument('--image', type=Path, required=True)
    sd.add_argument('--sha256', required=True)
    sd.add_argument('--inputs', type=Path, required=True)
    sd.add_argument('--run-dir', type=Path, required=True)
    sdhc = commands.add_parser('create-sdhc', help='Create a sparse SDHC FAT32 card (>2 GiB)')
    sdhc.add_argument('--image', type=Path, required=True)
    sdhc.add_argument('--size-gib', type=int, default=4)
    sdhc.add_argument('--cluster-kib', type=int, default=32)
    sdhc.add_argument('--label', default='SONY_SDHC')
    ext_bk = commands.add_parser(
        'extract-setting-backup',
        help='Extract /Backup.bin from a 20 MiB /setting FAT12 partition image',
    )
    ext_bk.add_argument('--part-image', type=Path, required=True)
    ext_bk.add_argument('--out', type=Path, required=True)
    media = commands.add_parser('sd-event', help='Insert or eject the predeclared card')
    media.add_argument('action', choices=('insert', 'eject'))
    media.add_argument('--qmp', type=Path, required=True)
    playback = commands.add_parser('verify-playback', help='Compare empty and inserted QEMU PPMs')
    playback.add_argument('--fixture', type=Path, required=True)
    playback.add_argument('--empty', type=Path, required=True)
    playback.add_argument('--inserted', type=Path, required=True)
    playback.add_argument('--image-box', type=int, nargs=4, metavar=('LEFT', 'TOP', 'RIGHT', 'BOTTOM'))
    capture = commands.add_parser('verify-photo', help='Decode a newly saved JPEG independently')
    capture.add_argument('--before', type=Path, required=True)
    capture.add_argument('--after', type=Path, required=True)
    capture.add_argument('--reopened', type=Path,
                         help='Separate DCIM export after reopening the card in a new QEMU process')
    args = parser.parse_args()
    try:
        if args.command == 'sd-blockdev':
            print(json.dumps(sd_blockdev(args.image, args.sha256, args.inputs, args.run_dir)))
        elif args.command == 'create-sdhc':
            print(json.dumps(create_sdhc_fat32_image(
                args.image, size_gib=args.size_gib,
                cluster_kib=args.cluster_kib, volume_label=args.label,
            ), indent=2))
        elif args.command == 'extract-setting-backup':
            print(json.dumps(extract_setting_backup_file(args.part_image, args.out), indent=2))
        elif args.command == 'sd-event':
            from tools.research.qemu_boot_probe import Qmp

            qmp = Qmp(args.qmp, time.monotonic() + 10)
            try:
                sd_event(qmp, args.action)
            finally:
                qmp.close()
        elif args.command == 'verify-playback':
            box = tuple(args.image_box) if args.image_box is not None else None
            print(json.dumps(verify_playback(args.fixture, args.empty, args.inserted, box), indent=2))
        else:
            print(json.dumps(verify_photo(args.before, args.after, reopened=args.reopened), indent=2))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
