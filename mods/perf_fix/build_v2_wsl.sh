#!/bin/bash
# Build PerfFix as an SMOD v2 (relocatable) mod for Mod Loader v2.
# Usage: build_v2_wsl.sh NEW_OUTPUT_DIR
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=$1
mkdir -p "$OUT"
DIR=mods/perf_fix
common=(-mthumb -mcpu=cortex-a5 -mfloat-abi=softfp -Os -ffreestanding -fno-builtin -nostdlib
        -nostartfiles -fno-stack-protector -fomit-frame-pointer -Wall -Wextra -Werror
        -fpie -fvisibility=hidden)

arm-linux-gnueabi-gcc "${common[@]}" -c "$DIR/perf_fix.c" -o "$OUT/perf_fix.o"
arm-linux-gnueabi-gcc "${common[@]}" -c "$DIR/perf_fix_trampoline.S" -o "$OUT/perf_fix_tramp.o"

for o in "$OUT/perf_fix.o" "$OUT/perf_fix_tramp.o"; do
  if arm-linux-gnueabi-readelf -rW "$o" | grep -E "R_ARM_(ABS32|GOT|TARGET1|PLT32|JUMP_SLOT|GLOB_DAT)"; then
    echo "$o has absolute or GOT relocations; it would not be position-independent" >&2
    exit 1
  fi
done

arm-linux-gnueabi-ld --build-id=none -T tools/native_camera/mlv2/mod_v2.ld \
  "$OUT/perf_fix.o" "$OUT/perf_fix_tramp.o" -o "$OUT/perf_fix.elf"

if arm-linux-gnueabi-objdump -d "$OUT/perf_fix.elf" | grep -qE "<(memcpy|memset|__aeabi_[a-z]*)>"; then
  echo "unexpected runtime helper in perf_fix.elf" >&2
  exit 1
fi

arm-linux-gnueabi-objdump -d "$OUT/perf_fix.elf" > "$OUT/perf_fix.dis"
arm-linux-gnueabi-nm -n "$OUT/perf_fix.elf" > "$OUT/perf_fix.sym"

python3 -m tools.research.pack_mod_v2 \
  --elf "$OUT/perf_fix.elf" \
  --name "PerfFix" \
  --on perf_on \
  --off perf_off \
  --bss 32 \
  --hook "0x060e81d4:2de9f04782b002af06468846:pf_hook_set_key_repeat" \
  --hook "0x060ffb10:2de9f04100f5b67500af0446:pf_hook_start_animate" \
  --hook "0x060ffe42:2de9f04700f5b678044600af:pf_hook_init_animate" \
  --hook "0x061325c8:f0b583b000afc0f800330446:pf_hook_flex_req_anim" \
  --hook "0x06100576:2de9f0479a46d0f89c3100af:pf_hook_grid_scroll_dir" \
  --hook "0x06100ffc:2de9f041044600af0d469046:pf_hook_grid_set_area_pos" \
  --hook "0x0610285a:2de9f04782b002af04461546:pf_hook_grid_scroll_iib" \
  --reloc-layout tools/native_camera/mlv2/reference_layout_ilce6400.txt \
  --out "$OUT/PerfFix.mod" | tee "$OUT/pack.json"
