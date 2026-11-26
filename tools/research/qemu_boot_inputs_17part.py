"""Prepare QEMU boot inputs from a 17-partition CXD90045 dump (no manifest.json).

Some captured dumps (e.g. ILCE-6400A) ship raw `nflasha1..18.bin` and
`nflashaB0/B1.bin` partition files without `manifest.json` or `nflasha-head-1m.bin`.
Given an SDM `82461.00` header file, this tool lays out `emmc.img` and writes a
boot-manifest.json that passes `qemu_boot_inputs verify`.
"""
from __future__ import annotations

import argparse
import io
import shutil
import tarfile
from pathlib import Path

from project_paths import PROJECT_ROOT
from tools.research.inventory import digest
from tools.research.qemu_boot_inputs import (
    EMMC_BOOT_PARTITION_BYTES, EMMC_USER_AREA_OFFSET, OUTPUTS,
    _fat_members, _write_json, boot_contract, parse_sdm_partitions,
)


def prepare(dump: Path, sdm_header: Path, output: Path, *, target_id: str = "ilce_6400a") -> dict:
    dump, sdm_header, output = dump.resolve(strict=True), sdm_header.resolve(strict=True), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    header = sdm_header.read_bytes()
    rows = parse_sdm_partitions(header)
    occupied = [r for r in rows if r["bytes"] and (dump / f"{r['device']}.bin").is_file()]
    if not occupied:
        raise ValueError(f"No matching nflasha*.bin partitions found in {dump}")
    prefix_size = occupied[0]["start"]
    if prefix_size > len(header):
        raise ValueError("SDM header is smaller than the first partition offset")
    captured_end = EMMC_USER_AREA_OFFSET + max(r["start"] + r["bytes"] for r in occupied)
    disk_size = 1 << (captured_end - 1).bit_length()

    inputs = []
    with (output / "emmc.img").open("xb") as disk:
        disk.truncate(disk_size)
        for index in range(2):
            boot_path = dump / f"nflashaB{index}.bin"
            if boot_path.is_file():
                data = boot_path.read_bytes()
                if len(data) > EMMC_BOOT_PARTITION_BYTES:
                    raise ValueError(f"{boot_path.name} exceeds {EMMC_BOOT_PARTITION_BYTES} bytes")
                disk.seek(index * EMMC_BOOT_PARTITION_BYTES)
                disk.write(data)
                inputs.append(dict(path=str(boot_path), bytes=len(data), sha256=digest(boot_path)))
        disk.seek(EMMC_USER_AREA_OFFSET)
        disk.write(header[:prefix_size])
        for row in occupied:
            part_path = dump / f"{row['device']}.bin"
            size = part_path.stat().st_size
            if size != row["bytes"]:
                raise ValueError(f"{part_path.name} is {size} bytes, expected {row['bytes']}")
            disk.seek(EMMC_USER_AREA_OFFSET + row["start"])
            with part_path.open("rb") as src:
                shutil.copyfileobj(src, disk, 16 * 1024 * 1024)
            inputs.append(dict(path=str(part_path), bytes=size, sha256=digest(part_path)))

    main = _fat_members((dump / "nflasha3.bin").read_bytes(), ("vmlinux.bin", "initrd.img", "av-cam.bin"))
    files = {
        "nflasha3-Image": main["vmlinux.bin"],
        "nflasha3-initrd": main["initrd.img"],
        "av-cam.bin": main["av-cam.bin"],
    }
    files.update(_fat_members((dump / "nflasha2.bin").read_bytes(), ("Backup.bin", "kemco.txt")))
    contract = boot_contract(files)
    files["serial-cmdline.txt"] = (contract["cmdline"] + "\n").encode("ascii")
    for name, data in files.items():
        (output / name).write_bytes(data)
    with tarfile.open(output / "setting.tar", mode="x", format=tarfile.USTAR_FORMAT) as archive:
        for name in ("Backup.bin", "kemco.txt"):
            info = tarfile.TarInfo("./" + name)
            info.size, info.mode = len(files[name]), 0o644
            archive.addfile(info, io.BytesIO(files[name]))

    report = {
        "schema_version": 2,
        "status": "completed",
        "target_id": target_id,
        "inputs": inputs,
        "boot_contract": contract,
        "emulated_disk_bytes": disk_size,
        "source_notes": [],
        "outputs": {name: dict(bytes=(output / name).stat().st_size, sha256=digest(output / name)) for name in OUTPUTS},
        "tool_sources": {
            "tools/research/qemu_boot_inputs_17part.py": digest(PROJECT_ROOT / "tools/research/qemu_boot_inputs_17part.py"),
            "tools/research/qemu_boot_inputs.py": digest(PROJECT_ROOT / "tools/research/qemu_boot_inputs.py"),
        },
    }
    _write_json(output / "boot-manifest.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--sdm-header", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--target-id", default="ilce_6400a")
    args = parser.parse_args(argv)
    report = prepare(args.dump, args.sdm_header, args.report_dir, target_id=args.target_id)
    print(report["status"], report["outputs"]["emmc.img"]["sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
