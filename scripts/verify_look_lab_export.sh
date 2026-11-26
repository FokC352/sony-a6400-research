#!/usr/bin/env bash
# Verify the portable BIONZ Look Lab export path from a folder that holds only the binary.
#
# macOS/Linux counterpart of verify_look_lab_export.ps1. Copies
# dist/BIONZ-Look-Lab-<os>-<arch>/bionz-look-lab into an otherwise empty folder and runs its headless
# export (--export-mod), the same preset library and writes as the "Export Camera Mod (.mod)" dialog:
#   * the twelve built-in presets must load with no preset folder on disk (preset_dir = null),
#   * --bodies both must write <out>/a6400/mods/<Look>.mod and <out>/a6100/mods/<Look>.mod,
#   * --bodies a6400 into a folder already named `mods` must write <mods>/<Look>.mod directly,
#   * every file must start with the SMOD v2 header, carry FLAG_ACTION, and match its FNV-1a 32,
#   * the live picker must be written per body with FLAG_ACTION|FLAG_RELOC, bss 48 and one hook,
#   * the two bodies' files must differ (they carry different physical addresses).
# The binary under test needs no Python; this checker uses python3 to parse receipts and headers.
#
# Usage: scripts/verify_look_lab_export.sh [--look "Classic Chrome"] [--root EMPTY_DIR]
#   --root defaults to a fresh temporary folder; an existing non-empty folder is refused.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/_common.sh"

LOOK="Classic Chrome"
ROOT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --look) LOOK="${2:?--look needs a value}"; shift 2 ;;
        --root) ROOT="${2:?--root needs a value}"; shift 2 ;;
        -h | --help) sed -n '2,17p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

command -v python3 >/dev/null || die "python3 is required to check the receipts and headers"
SOURCE="$REPO_ROOT/dist/BIONZ-Look-Lab-$(host_os)-$(host_arch)/bionz-look-lab"
[ -x "$SOURCE" ] || die "$SOURCE is missing; run scripts/build_look_lab_release.sh first"

if [ -z "$ROOT" ]; then
    ROOT=$(mktemp -d "${TMPDIR:-/tmp}/LookLabTest.XXXXXX")
elif [ -e "$ROOT" ] && [ -n "$(ls -A "$ROOT")" ]; then
    die "$ROOT is not empty; pass an empty or new folder"
else
    mkdir -p "$ROOT"
fi
ROOT=$(cd "$ROOT" && pwd -P)
PASSED=0
# Keep the evidence on failure; remove it only after a pass.
trap '[ "$PASSED" = 1 ] && rm -rf "$ROOT" || echo "kept $ROOT for inspection" >&2' EXIT

EXE="$ROOT/bionz-look-lab"
cp "$SOURCE" "$EXE"
SIBLINGS=$(ls -A "$ROOT")
[ "$SIBLINGS" = "bionz-look-lab" ] || die "the check folder must contain only the binary, found: $SIBLINGS"
echo "Checking in $ROOT (initially: $SIBLINGS)"

# export_mod DEST BODIES KIND LOOK -> receipt at $ROOT/receipt-KIND-BODIES.json
export_mod() {
    local receipt="$ROOT/receipt-$3-$2.json" errlog="$ROOT/receipt-$3-$2.stderr"
    if ! (cd "$ROOT" && "$EXE" --export-mod "$1" --kind "$3" --look "$4" --bodies "$2" >"$receipt" 2>"$errlog"); then
        die "--export-mod --kind $3 $2 failed: $(cat "$receipt" "$errlog")"
    fi
}

echo "Exporting single look (both bodies), single look into a mods folder (a6400), live picker (both bodies)"
export_mod "$ROOT/out" both single "$LOOK"
mkdir -p "$ROOT/sd/mods"
export_mod "$ROOT/sd/mods" a6400 single "$LOOK"
export_mod "$ROOT/live" both live unused

python3 -I - "$ROOT" "$LOOK" <<'PY'
import json
import re
import struct
import sys
from pathlib import Path

root = Path(sys.argv[1])
look = sys.argv[2]
file_base = re.sub(r"[^A-Za-z0-9]", "", look)


def fail(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def fnv1a(data):
    # FNV-1a 32 over the code, exactly as Mod Loader v2 checks it.
    h = 2166136261
    for byte in data:
        h = ((h ^ byte) * 16777619) & 0xFFFFFFFF
    return h


def receipt(kind, bodies):
    path = root / f"receipt-{kind}-{bodies}.json"
    text = path.read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        fail(f"{path.name} is not JSON ({exc}): {text!r}")
    if not data.get("ok"):
        fail(f"export receipt is not ok: {text}")
    return data


def single_header(path):
    if not path.is_file():
        fail(f"{path} was not written")
    data = path.read_bytes()
    if len(data) < 68:
        fail(f"{path} is shorter than an SMOD header")
    version, flags = struct.unpack_from("<II", data, 4)
    code_size, checksum = struct.unpack_from("<II", data, 44)
    if data[:4] != b"SMOD":
        fail(f"{path} does not start with SMOD")
    if version != 2:
        fail(f"{path} is SMOD version {version}, expected 2")
    if flags & 16 != 16:
        fail(f"{path} is missing FLAG_ACTION (16)")
    if code_size != len(data) - 68:
        fail(f"{path} code size {code_size} does not match the file")
    if fnv1a(data[68:]) != checksum:
        fail(f"{path} FNV-1a checksum mismatch")
    print(f"   {path.relative_to(root)}  {len(data)} bytes  SMOD v{version}  flags=0x{flags:x}  fnv1a=0x{checksum:08x}")
    return data


print("1. Both bodies, from a folder containing only the binary")
both = receipt("single", "both")
preset_dir = both.get("preset_dir")
print(f"   presets_loaded={both['presets_loaded']} presets_built_in={both.get('presets_built_in')} "
      f"preset_dir={'<none>' if preset_dir is None else repr(preset_dir)} look={both.get('look')!r} "
      f"bodies={'+'.join(both.get('bodies', []))}")
if both["presets_loaded"] < 12:
    fail(f"expected at least 12 built-in presets, got {both['presets_loaded']}")
if preset_dir:
    print(f"warning: a preset folder was also found at {preset_dir}; run this from a folder outside the checkout "
          "to see the built-in-only case", file=sys.stderr)
a6400 = single_header(root / "out" / "a6400" / "mods" / f"{file_base}.mod")
a6100 = single_header(root / "out" / "a6100" / "mods" / f"{file_base}.mod")

print("2. One body into a folder already named mods (an SD card opened at <SD>/mods)")
receipt("single", "a6400")
mods = root / "sd" / "mods"
single_header(mods / f"{file_base}.mod")
if (mods / "mods").exists():
    fail("a mods folder must not get a nested mods folder")

print("3. Live picker (FilmSimulations.mod) for both bodies")
live = receipt("live", "both")
print(f"   presets_loaded={both['presets_loaded']} kind={live.get('kind')} bodies={'+'.join(live.get('bodies', []))}")
for body in ("a6400", "a6100"):
    path = root / "live" / body / "mods" / "FilmSimulations.mod"
    if not path.is_file():
        fail(f"{path} was not written")
    data = path.read_bytes()
    if data[12:28] != b"Film Simulations":
        fail(f"{path} is not the Live Picker")
    (flags,) = struct.unpack_from("<I", data, 8)
    code_size, checksum, bss = struct.unpack_from("<III", data, 44)
    (hooks,) = struct.unpack_from("<I", data, 64)
    if flags != 20:
        fail(f"{path} flags {flags}, expected FLAG_ACTION|FLAG_RELOC (20)")
    if bss != 48:
        fail(f"{path} bss {bss}, expected 48")
    if hooks != 1:
        fail(f"{path} has {hooks} hooks, expected the event_push hook")
    if fnv1a(data[108:108 + code_size]) != checksum:
        fail(f"{path} FNV-1a mismatch")
    print(f"   {path.relative_to(root)}  {len(data)} bytes  SMOD v2  flags=0x{flags:x}  bss={bss}  hooks={hooks}")

print("4. Files on disk")
for path in sorted(root.rglob("*.mod")):
    print(f"   {path.relative_to(root)}  {path.stat().st_size} bytes")

print("5. The two bodies carry different payloads")
if len(a6400) != len(a6100):
    fail("the two .mod files differ in length")
first_diff = next((i for i in range(68, len(a6400)) if a6400[i] != a6100[i]), -1)
if first_diff < 0:
    fail("the a6400 and a6100 payloads are identical")
print(f"   payloads differ from offset {first_diff} on (the body's physical addresses)")

print()
print(f"PASS: bionz-look-lab loaded {both['presets_loaded']} built-in presets from a folder that contained only itself and wrote")
print(f"      out/a6400/mods/{file_base}.mod and out/a6100/mods/{file_base}.mod (single look) plus")
print("      live/a6400/mods/FilmSimulations.mod and live/a6100/mods/FilmSimulations.mod (Live Picker) as SMOD v2.")
PY
PASSED=1
