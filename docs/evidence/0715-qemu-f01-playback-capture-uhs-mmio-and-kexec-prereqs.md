# E0715 — QEMU `sony-a6400` Playback/Capture Codecs, UHS-I `U3` SDHC, Zero-Unmodelled MMIO / Clock-Reset / PMIC Models, `ILCE-6400A` `IDT_CAM`, `IMX284` P2S, and Outer-L2 (`PL310`) Handover Guard

```text
ID: E0715
Date: 2026-11-26
Claim: Implements and qualifies the out-of-tree QEMU v11.1.3 CXD90045 prerequisites across qemu-a6400/, src/a6400/sensor/, and tools/research/: (1) pure-C ITU-T T.81 baseline 4:2:2/4:2:0 JPEG decode (op=0x11) and ECS encode (op=0x1B) on ZMA0/ZMA1, RcDriver UYVY/Bayer resize on RC0 (0xF3880000), VPH ch51/ch52 packed UYVY BT.601 RGB888 scanout, active 0xF3030000 IDT/RCRAW ARW 2.3 cRAW2 16-byte block compression (IRQ 88), FE-AIF RCRAW IRQs 70..72, and post-boot GPIO-driven Sub-Micro CAIF shutter frames; (2) UHS-I 1.8V CMD11 voltage switch, SCR[33] CMD23 bit, CMD19 SDR104 tuning, ACMD13 U3 speed-class SSR, mmc0-vendor (+0x100..+0x4FF) read-back, 4 GiB SDHC FAT32 media builder, and SFRS/DVENC movie-codec HLE; (3) retirement of all remaining E0705 unmodelled MMIO pairs (0 distinct unmodelled lines in live full-peripheral boot), verified 0x10-stride STAT/SET/CLR clock/reset banks (0xF2800000, 0xF2900000, 0xF2A00000..0xF2A32000), ZMA0/RC0/SA4 PBL cores, and MISCREG 0xF2915040 SWRESET/XRSTREQ + SRAM GPIO P10.5 (cfg.poff=10,5) power-off; (4) natural av-cam step-26 -> step-42 (0x0102) progression without GATE_FORCE_PROBE or ENTRY_FORCE_PROBE, 256-byte SPI Slot-C fix retiring ILCE-6400A 0x63909ba8 ('S-Ele Refresh ng force exception') with IDT_CAM sustained bars live-view, sub-micro Slot A[29] BIT(6) HDMI HPD reporting, and persistent /setting/Backup.bin overlay reload; and (5) P2S Ch 0/1 256-slot IMX284 13-column / 19-mode recognizer plus hardened G13 outer-L2 (PL310) cache-teardown verification in handover_guard.
Status: verified-offline (6/6 QemuPrerequisitesHardwareTests + 9/9 HandoverGuardTests + 8/8 QemuPeripheralsAndModelsTests + 8/8 QemuLiveViewPipelineTests + 4/4 Imx284P2sTests + 25/25 static overlay checks + 112/112 standalone rule-window qtests PASS; live ILCE-6400 2.00 and ILCE-6400A 1.01 QEMU boots verified without force probes)
```

## Input and Tool Identities

| Artifact / Binary | Size | SHA-256 |
| --- | ---: | --- |
| Built QEMU `qemu-system-arm` (`v11.1.3` + `qemu-a6400` overlay) | — | `f861dd07f83ba335a0d469d83a8128c855c98739c6995256a781567915535abd` |
| Upstream QEMU base commit (`v11.1.3`) | — | `005c61c0ef6f6ae615283440b4bb7be51b2c6ddd` |
| `ILCE-6400` 2.00 captured `nflasha3-Image` | 4,085,888 B | `d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618` |
| `ILCE-6400` 2.00 captured `av-cam.bin` | 16,803,048 B | `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209` |
| `ILCE-6400` 2.00 captured `emmc.img` (unchanged before/after) | 2,147,483,648 B | `18e37ec3b3e56d26d31c0b0a4fd91ff10d0d1cb8a23308e49c35abd5cd3cc94b` |
| `ILCE-6400A` 1.01 packaged `vmlinux.bin` (`nflasha3`) | 4,090,624 B | `9d51ab60df8246f10cb2afb12fb504d7f148fae437ee404f5b9837cc3b8f85ca` |
| `ILCE-6400A` 1.01 packaged `av-cam.bin` (`nflasha3`) | 16,824,808 B | `3029a174b393fee9b2624d27737c20bc60e71cbbb33736b2a9363b57184ee03d` |
| `sabin/idt_cam.bin` (`ILCE-6400` & `ILCE-6400A`) | 176,528 B | `d21e2c0b6b87f49f70ccf451781093037db8093b5a378587ec0029a16dcaef92` |
| `sabin/sa_zima_startup_zma1.bin` (`+0x200` 2,692 B payload) | 3,204 B | `fc668233…` (`+0x200` payload: `46a92d9fc1c715a13477f9792c623ca633598b6595978ad761551570e31eaff4`) |
| `ILCE-6400` 2.00 `mmc_drv.ko` | — | `b150c666ee29567e920e156b8a5def8307d04c51b799956cdd74fcf5c55211cf` |
| Powered WBI sub-micro extract (`ILCE-6400` 2.00) | 384 B | `3385ec340e931aa7b7dbb954da99e5b6239174ba90441eb2c905f01bcf5bbda9` |

## Firmware & Hardware Contracts Established

### 1. Stock JPEG Playback, Still Capture Codecs, and `VPH` `UYVY` Scanout (`qemu-a6400:F01`)

- **Why earlier seeded JPEGs failed (`0xFB030005` -> `0x1600F301` -> `"Unable to display"`):** `av-cam.bin` `dgis_jpeg_header_parse` (`0x63A053A8`) enforces baseline `SOF0` `YCbCr 4:2:2` (`H1V1 = 0x21`) or `4:2:0` (`0x22`), rejects `4:4:4` (`0x11`) and optimized non-Annex-K `DHT` tables (`0x63A08950`), and requires valid TIFF/Exif metadata before submitting DGI decode `op = 0x11` (`0x01001100`) to `ZMA0`/`ZMA1`. Pillow's default `4:2:0`/JFIF or optimized-Huffman output returned `0xFB030005` before `ZMA1` decode was ever queued, falling back to `op = 0x13` (`dgi_jpeg_fill`) neutral grey fill.
- **DGI JPEG Decode (`op = 0x11`), Encode (`op = 0x1B`), and `ARW 2.3` `cRAW2` (`cxd90045_jpeg.c`):**
  - `cxd90014_sa.c` (`zma1-jpeg-decode=on`) recognizes both `ZMA0` (`83a3e61b…`) and `ZMA1` (`46a92d9f…`) startup payloads, decodes real baseline `SOF0` `4:2:2`/`4:2:0` JPEGs (`d+0x34`..`d+0x38`) with crop/resize (`d+0x0C`, `d+0x18`, `d+0x1C`, `d+0x20`) into packed 16-bpp `UYVY` (`Cb0 Y0 Cr0 Y1`) at `d+0x44`, and writes `1` to report `*(d+0x5C)+4` before raising factor `0x01`.
  - `op = 0x1B` (`0x02001B00`) encodes `UYVY` buffers using the descriptor's big-endian-in-`u32` luma/chroma quantization tables (`d+0x80`, `d+0xC0`), standard Annex K Huffman tables, `0xFF 0x00` byte-stuffing, `DRI` restart markers (`0xFFD0..0xFFD7` every `d+0x08` MCUs), and `EOI` (`0xFFD9`) at `out_pa + (d+0x04)`, reporting exact ECS byte length at `*(d+0x5C)+8`.
  - `cxd90045_cam.c` (`RcDriver` on `RC0` `0xF3881000`) performs DRAM-to-DRAM `UYVY` nearest-neighbor resize when `ch0` bit 11 (`0x800`) is set instead of overwriting playback/thumbnail buffers with live-view bars, and `cxd90014_vph.c` scans out packed `UYVY` on `ch51`/`ch52` via BT.601 RGB888 conversion.
  - Under `still-capture=on`, `sony_a6400.c` maps an active `0xF3030000` `IDT/RCRAW` engine (IRQ `88`, preserving all 522 `E0702` self-clearing `ZERO` offsets while serving `0x00040000` Init, `0x00060000/1` 16-byte Sony `ARW 2.3` `cRAW2` compression, and `0x00050004` decompression), wires `FE-AIF` IRQs `70..72`, and models the post-boot GPIO-driven Sub-Micro CAIF protocol (`P7.15` `S2REQ`, `P12.0` `XVS`, `P2.5` `SM_REQ`, `SIO ch3` `(3, 0)` `CAP_START` & `(3, 1)` `EXP_INFO`).

### 2. Virtual SD UHS-I `U3` Qualification & Movie Codec HLE (`sd-uhs-u3=on`, `movie-codec-hle=on`)

- **Why stock `mmc_drv.ko` (`b150c666…`) rejected virtual cards for UHS-I / `XAVC S 4K`:**
  1. `libInfraMediaCommon.so` requires SDHC/SDXC (`> 2 GiB` FAT32/exFAT) plus `ACMD13` SD Status `SPEED_CLASS = 0x04` (Class 10, `sd_status[8]`), `AU_SIZE = 0x9` (`4 MiB`, `sd_status[10]`), and `UHS_SPEED_GRADE = 3` (`U3`, `sd_status[14] = 0x39`).
  2. Live disassembly of `mmc_drv.ko` revealed two additional hardware gates during `MMCD_IOCTL_MOUNT`:
     - At `.text+0x0BABC`..`0x0BAEC`, `mmc_drv.ko` writes `0x00080010` / `0x00040010` to Sony's vendor SDHCI PHY register **`0xF0300204`** (`mmc0 + 0x204`) and verifies `(readl(+0x204) & 0x03FF0000)` matches before issuing UHS commands; mapping `cxd90045.mmc0-vendor` (`0xF0300100..0xF03004FF`) satisfies this read-back and retires 37 runtime unmodelled accesses.
     - At `.text+0x17C60`..`0x17C88` (`mmc_mmcp_check_speed_uhs_all` caller), whenever `CMD6` selects `SDR104` (`mode = 3`), `mmc_drv.ko` checks **`SCR[33]` (`sd->scr[3] & 0x02`, `CMD23` `SET_BLOCK_COUNT` support, mandatory per SD Physical Layer Spec v3.01 Table 5-17)**. Upstream QEMU v11.1.3 implemented `sd_cmd_SET_BLOCK_COUNT` (`CMD23`) yet left `sd->scr[3] = 0x00`, causing `mmc_drv.ko` to abort mount with `"Unable to use memory card."`. Setting `sd->scr[3] = 0x02` when `uhs_speed_grade > 0` lets live `mmc_drv.ko` complete `CMD11` 1.8V switch, `[MMCP][mmc_mmcp_exec_tuning] For F_SDH30` (`CMD19` tuning), and `mmca10: p1` partition registration on a 4 GiB UHS-I `U3` SDHC FAT32 image.
- **Movie Encoder HLE (`cxd90014_sa.c`):** `movie-codec-hle=on` handles `SFRS` Audio SA (`0xF3590000`) commands `1..7` + 21.33 ms AAC/AC-3/LPCM frame ticks (`IRQ 60`) and `ZIMA DVENC` (`0xF34B0000`) H.264 Annex-B IDR/non-IDR access-unit synthesis (`IRQ 54`) behind strict opt-in gating.

### 3. Zero-Unmodelled MMIO Coverage, Verified Clock/Reset/PBL Models, and PMIC/Reset (`clkrst-model=on`, `pmic-reset=on`)

- **Retirement of remaining `E0705` unmodelled MMIO pairs:** Added targeted `-600` peripheral models/windows for `GP-DMAC1` (`0xF0103000`), `emmc0-vendor` (`0xF0304100`), `spi0` (`0xF2006000`), `sircs` (`0xF2400000`), `combophy-rx/mon` (`0xF2931000..0xF2932FFF`), `pbl-aic0` (`0xF30A0000`), `dfe2` (`0xF3200000`), `audioip` (`0x9000` span), `cal-nor` (`0xF6000000`), `cxd90045-i2c` pre-write status/timing reads, and `cxd90045-tpu` `+0x10` status reads. Live `ILCE-6400` 2.00 full-peripheral boot (`artifacts/qemu-a6400/e0707-live-a6400/`) recorded **`0` distinct unmodelled MMIO lines** (`47 -> 0`).
- **Stateful Clock/Reset & PBL Models (`clkrst-model=on`, priority `-450`):** Replaces plain `ZERO`/read-back windows on `0xF2800000`, `0xF2900000`, `0xF2A00000..0xF2A32000`, `0xF3500000`, and `0xF3880000` with verified `0x10`-stride `STAT(+0x0)/SET(+0x4 W1S)/CLR(+0x8 W1C, reads 0)` clock/reset banks, `CRCDC` `+0x190 = 0x3F`, `ZMA0` (`0xF3500000`) + `RC0` (`0xF3880000`) PBL IP blocks, and a 5th `SA4` core (`0xF35D0000`), while keeping bare `-M sony-a6400` static/qtest baselines (`test_qemu_f290.py`, `test_qemu_f2a0.py`, `test_qemu_l0137_sa4_id.py`) untouched.
- **PMIC Keepalive / Warm Reset / Power-Off (`pmic-reset=on`):** Models `MISCREG` `0xF2915040` (`SWRESET` bit 0 -> `SHUTDOWN_CAUSE_GUEST_RESET` when `XRSTREQ` `+0x1044` bit 0 == 1), `DDR-A`/`DDR-B` self-refresh (`0xEA`/`0x1AE`), and `GPIO P10.5` (`out[325]`, kernel `cfg.poff=10,5` SRAM power-off sequence) -> `SHUTDOWN_CAUSE_GUEST_SHUTDOWN`, plus `PERSIST_EMMC_OVERLAY` `/setting/Backup.bin` extraction and reload across restarts in `qemu-a6400/run.sh`.

### 4. Natural Step-26 -> Step-42 Progression, `ILCE-6400A` `IDT_CAM`, `IMX284` P2S, and `PL310` Handover Guard

- **Natural step-26 -> step-42 (`0x0102`) without force probes:** Latching valid sub-micro TLV `0x0A` lens-rail requests across Slot-B polls (`sony_a6400_sm_rail_on` under `submicro-lens-power=on`) lets `av-cam` transition naturally (`S = 0xF0 -> 0x03 -> 0x00`) and reach **`0x0102` (step 42 of 43)** with **`GATE_FORCE_PROBE=0` and `ENTRY_FORCE_PROBE=0`**. Disassembly of `0x6386624E..0x6386626C` further showed that attaching a virtual E-mount lens (`P1.4` asserted via `PERIPHERALS=1` default `selp1650`) disables the lensless body-ready helper `0x6385D51C` and holds module 3 at `0x0323/0x0320` until full `BL` lens handshake completes, whereas lensless stock boot advances directly to `0x0102`.
- **`ILCE-6400A` 1.01 `0x63909ba8` retirement & `IDT_CAM` live-view (`artifacts/qemu-a6400/e0707-live-a6400a/`):** In `Mode 1` 256-byte SPI transfers (`tx[0]=0x21, tx[128]=0x61`), `sony_a6400_submicro_xfer` previously copied `+256` (`Slot B`, `0x30`) into the second 128-byte half instead of `+128` (`Slot C`, `0x60`), causing `ILCE-6400A` `av-cam.bin` (`3029a174…`) to reject the sub-micro refresh and deliberately crash at `0x63909ba8` (`str r7,[r7,#0]`, `'FW: S-Ele Refresh ng force exception'`). Fixing `sony_a6400_submicro_slot_offset` retires `0x63909ba8` completely: unmodified `ILCE-6400A` 1.01 with `idt_cam.bin` (`d21e2c0b…`) boots in 42 s with `0` exceptions, `52` P2S sensor frames, and sustained SMPTE bars + interactive shooting HUD (`01_a6400a_boot.png`, `02_a6400a_liveview.png`).
- **`IMX284` 13-Column / 19-Mode P2S Correlation (`src/a6400/sensor/imx284_p2s.py`, `cxd90014_p2s.c`):** Maps `P2S` Ch 1 (`0xFC088000`, IRQ `82`, 256 slots `+0x340..+0x73C`), decodes `[0x81, len, chip_id, addr_hi, addr_lo, data...]` bursts, extracts all 125 static rows across 13 columns from `av-cam.bin` (`0x63E0A99C`, `reg_addr = *(desc_ptr - 2)`), and correlates captured LiveView P2S traffic to **Column 3 (`liveview_1996x570_11bit`, modes `4/5`, `pgc=0x038F`, `xhs=0x006A`, `xvs=0x046C`)**.
- **Outer-L2 (`PL310`) `handover_guard` Teardown (`G13`):** Extended `qemu-a6400/tools/handover_guard.c` and `sony_a6400.c` (`pl310-ext`: `CACHE_TYPE = 0x1C100100`, PMU/IRQ `+0x200..+0x224`, 8-CPU lockdown `+0x900..+0x93C`, `DEBUG_CTRL` `+0xF40`) to enforce `SCTLR.C == 0` before L2 clean+inv (`0x7FC` full way-mask `0xFF`/`0xFFFF` **or** Linux's `cache_l2x0.wait=3` `outer_disable` index-by-way `0x7F8` loop + `0x7FC` poll == 0), zero lockdown/debug hazards, `CACHE_SYNC` (`0x730`), `CTRL = 0` (`0x100`) **after** flush/sync, and a post-disable `DSB` before entry (`9/9` assembly fault-injection tests PASS).

## Verification Commands and Receipts

```bash
# 1. Native WSL qtest / QMP / TCG-plugin verification against f861dd07...
A6400_QEMU_DIR=/home/administrator/sony-a6400-qemu-11.1.3 \
A6400_ZMA1_STARTUP_SIDECAR="firmware/extracted_a6400a_v101/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin/sa_zima_startup_zma1.bin" \
python3 -m unittest \
  tests/test_qemu_clkrst_pmic_uhs_hdmi.py \
  tests/test_qemu_handover_guard.py \
  tests/test_qemu_peripherals_and_models.py \
  tests/test_qemu_liveview.py \
  tests/test_imx284_p2s.py \
  tests.test_qemu_media.QemuRemovableSdTests \
  tests.test_qemu_media.QemuUsbMassStorageTests \
  tests.test_qemu_media.MediaInputTests.test_sdhc_fat32_builder_and_setting_backup_extractor -v
# -> 39/39 PASS (0 failures, 0 skips)

# 2. Baseline static & standalone rule-window qtests (qemu-a6400/tests/)
A6400_QEMU_DIR=/home/administrator/sony-a6400-qemu-11.1.3 \
QEMU_BIN=/home/administrator/sony-a6400-qemu-11.1.3/build/qemu-system-arm \
python3 -m unittest \
  qemu-a6400/tests/test_qemu_f290.py \
  qemu-a6400/tests/test_qemu_f291_l0117.py \
  qemu-a6400/tests/test_qemu_f2a0.py \
  qemu-a6400/tests/test_qemu_f312_l0119.py \
  qemu-a6400/tests/test_qemu_f3c0.py \
  qemu-a6400/tests/test_qemu_l0137_sa4_id.py \
  qemu-a6400/tests/test_qemu_l0154_dwc3_default.py \
  qemu-a6400/tests/test_qemu_l0159_watchdog_default.py -v
# -> 25/25 PASS; standalone f022 (12/12), f292 (18/18), f292c (16/16), f304 (18/18), rcraw (41/41), uart3 (7/7) -> 112/112 PASS
```

- **Live firmware receipts:** `artifacts/qemu-a6400/e0707-live-a6400/` (`ILCE-6400` 2.00: `mmca10: p1` UHS-I `U3` SDR104 mount, `0` distinct unmodelled MMIO lines, natural `0x0102` step-42 without force probes, `08_hdmi_plugged_tv.png` `head=1` HDMI output, `/setting/Backup.bin` `1,171,036 B` overlay persistence) and `artifacts/qemu-a6400/e0707-live-a6400a/` (`ILCE-6400A` 1.01: `01_a6400a_boot.png`, `02_a6400a_liveview.png`, `s_ele_exception = false`).

## Assumptions, Boundaries, and Remaining Open Items

- **Emulator-only scope:** No physical camera, USB device, or physical SD card was accessed. Base `emmc.img` SHA-256 (`18e37ec3b3e56d26…`) was verified unchanged after all runs.
- **Codec & peripheral fidelity vs full DSP execution:** `cxd90045_jpeg.c` (`op=0x11` decode, `op=0x1B` encode, `cRAW2` pack/unpack) and `movie-codec-hle` (`SFRS`/`DVENC`) implement deterministic C functional contracts over guest DRAM descriptors rather than executing proprietary ZIMA/SFRS DSP microcode instructions.
- **Interactive UI timing under captured 10-second Power Save (`C20`):** The hardware/DMA paths for `ZMA1` `op=0x11` decode -> `RC0` -> `VPH` `UYVY` RGB888 scanout, `op=0x1B` encode, `cRAW2` compression, CAIF shutter frames, and `GPIO P10.5` shutdown are verified end-to-end in native QEMU `qtest`/QMP (`test_qemu_clkrst_pmic_uhs_hdmi.py`). In full WBI guest boots with the captured `10 s` Power Save setting (`E0652`) and newly mounted 4 GiB SDHC cards, `ModelRepair` / `EE_STOP` (`0x010e`) and `ModelRemoteAsync::Suspend` remain timing-sensitive to guest inactivity and wireless daemon state; interactive menu/playback/recording flows in `rear_panel.py` should keep the camera awake or set a longer Power Save timeout before multi-step UI automation.
- **Physical `kexec` gate:** `G1`–`G13` (including outer-L2 `PL310` teardown, DMA quiescence, watchdog, and `SWRESET`/`GPIO P10.5` power-off) are now enforced in `handover_guard` in emulation; any physical RAM-only `kexec` trial still requires explicit per-incident user authorization and a verified target payload.
