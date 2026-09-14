#!/bin/bash
# Build the MojoMod demo: a RISC OS relocatable module whose body is Mojo,
# plus swicall, an ordinary application that calls the module's SWIs.
#
#   tools/build-module.sh            (from compiler/)
#
# Chain, in order:
#   gen_module.py  -> module header, tables and entry veneers (.s)
#   clang -fropi -frwpi -> the runtime: code PC-relative, statics r9-relative
#   mojo --target-features +reserve-r9 -> the body, leaving r9 alone
#   roscc --module -> base-0 image, two address spaces, header checked, ,ffa
#
# The static-base discipline is the whole reason the runtime works inside a
# module: every writable static becomes an offset from r9, the linker lays
# them out in their own space, and the module's initialisation claims RMA and
# points r9 at it. Everything on the call path must agree to leave r9 alone,
# which is what -frwpi and +reserve-r9 each promise.
#
# Output: tests/module/build/mojomod,ffa and swicall,ff8 (+ .elf sidecars).

set -e
cd "$(dirname "$0")/.."

LLVM="/c/Program Files/LLVM/bin"
MOJO="/f/RISCOSDEV/mojo-riscos/bazel-bin/KGEN/tools/mojo/mojo.exe"
MOJO_ROOT="/f/RISCOSDEV/mojo-riscos"
OUT=tests/module/build
TRIPLE=${TRIPLE:-armv4-none-eabi}
CPU=${CPU:-strongarm110}
ARCH=${ARCH:-armv4}

# A module claims its whole static area from the RMA at initialisation, so
# the application defaults (256K heap, 32K arena) are the wrong size here.
MOD_ARENAS="-DHEAP_BYTES=32768 -DARENA_BYTES=8192"

mkdir -p "$OUT"

echo "== header + veneers"
# Chunk &C7000: bits 19:18 = 11 is the user-application range (PRM 1-27),
# and it is a multiple of 64 with a zero top byte, which is what the kernel
# checks before it will honour the SWI fields at all.
python tools/gen_module.py \
    --title MojoMod \
    --help-text 'MojoMod\t1.00 (09 Sep 2026)' \
    --rwpi --workspace 256 \
    --init mojomod_init --final mojomod_final \
    --command 'MojoMod:mojomod_command:0:1:*MojoMod runs the Mojo code in this module\rSyntax:\t*MojoMod' \
    --swi-chunk 0xC7000 --swi-prefix MojoMod \
    --swi 'Add:mojomod_swi_add' \
    --swi 'Counter:mojomod_swi_counter' \
    --arch "$ARCH" \
    -o "$OUT/module_head.s"

"$LLVM/clang.exe" --target=$TRIPLE -mcpu=$CPU -c "$OUT/module_head.s" \
    -o "$OUT/module_head.o"

echo "== runtime, static-base relative"
for src in rostrt/rostrt.c rostrt/swis_os.c tests/module/rwpi_check.c; do
    base=$(basename "$src" .c)
    "$LLVM/clang.exe" --target=$TRIPLE -mcpu=$CPU -mfloat-abi=soft \
        -ffreestanding -nostdlib -fno-builtin -fropi -frwpi -O0 $MOD_ARENAS \
        -c "$src" -o "$OUT/$base.o"
done
"$LLVM/clang.exe" --target=$TRIPLE -mcpu=$CPU -mfloat-abi=soft \
    -c rostrt/aeabi.s -o "$OUT/aeabi.o"
"$LLVM/clang.exe" --target=$TRIPLE -mcpu=$CPU -mfloat-abi=soft \
    -c rostrt/atomics_swp.s -o "$OUT/atomics.o"

echo "== module body (Mojo)"
"$MOJO" build --emit object \
    -I "$MOJO_ROOT/mojo/stdlib" -I "$MOJO_ROOT" \
    --target-triple $TRIPLE --target-cpu $CPU \
    --target-features +reserve-r9 \
    -o "$OUT/mojomod.o" tests/module/mojomod.mojo

echo "== link"
./target/debug/roscc.exe link --module -o "$OUT/mojomod,ffa" \
    "$OUT/module_head.o" "$OUT/mojomod.o" "$OUT/rostrt.o" "$OUT/modrt.o" \
    "$OUT/swis_os.o" "$OUT/rwpi_check.o" "$OUT/aeabi.o" "$OUT/atomics.o"

# The other half of the SWI test: an ordinary Absolute image that calls the
# module the way any program would. Built with the application runtime, so
# it shares nothing with the module but the published SWI interface.
echo "== caller application"
"$LLVM/clang.exe" --target=$TRIPLE -mcpu=$CPU -mfloat-abi=soft \
    -ffreestanding -nostdlib -fno-builtin -O1 \
    -c tests/module/swicall.c -o "$OUT/swicall_shim.o"

"$MOJO" build --emit object \
    -I "$MOJO_ROOT/mojo/stdlib" -I "$MOJO_ROOT" \
    --target-triple $TRIPLE --target-cpu $CPU \
    -o "$OUT/swicall.o" tests/module/swicall.mojo

./target/debug/roscc.exe link --rt "${RT:-sa}" -o "$OUT/swicall,ff8" \
    "$OUT/swicall.o" "$OUT/swicall_shim.o"

ls -l "$OUT/mojomod,ffa" "$OUT/swicall,ff8"
