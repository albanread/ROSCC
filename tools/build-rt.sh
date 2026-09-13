#!/bin/bash
# Build the rostrt runtime objects for both RISC OS profiles.
# Usage: tools/build-rt.sh        (from compiler/)

set -e
# The clang bin directory: override for your host (macOS:
#   LLVM=/usr/local/opt/llvm@22/bin tools/build-rt.sh).
LLVM="${LLVM:-/c/Program Files/LLVM/bin}"
CLANG="$LLVM/clang"
cd "$(dirname "$0")/.."
mkdir -p rostrt/build

build_profile () {
    local triple=$1 cpu=$2 tag=$3
    "$CLANG" --target=$triple -mcpu=$cpu -mfloat-abi=soft \
        -ffreestanding -nostdlib -fno-builtin -O1 \
        -c rostrt/rostrt.c -o rostrt/build/rostrt-$tag.o
    "$CLANG" --target=$triple -mcpu=$cpu -mfloat-abi=soft \
        -ffreestanding -nostdlib -fno-builtin -O1 \
        -c rostrt/wimp.c -o rostrt/build/wimp-$tag.o
    for swi_c in rostrt/swis_*.c; do
        base=$(basename "$swi_c" .c)
        "$CLANG" --target=$triple -mcpu=$cpu -mfloat-abi=soft \
            -ffreestanding -nostdlib -fno-builtin -O1 \
            -c "$swi_c" -o "rostrt/build/$base-$tag.o"
    done
    "$CLANG" --target=$triple -mcpu=$cpu -mfloat-abi=soft \
        -c rostrt/crt0.s -o rostrt/build/crt0-$tag.o
    "$CLANG" --target=$triple -mcpu=$cpu -mfloat-abi=soft \
        -c rostrt/aeabi.s -o rostrt/build/aeabi-$tag.o
    if [ "$tag" = sa ]; then
        "$CLANG" --target=$triple -mcpu=$cpu -mfloat-abi=soft \
            -c rostrt/atomics_swp.s -o rostrt/build/atomics-$tag.o
    else
        "$CLANG" --target=$triple -mcpu=$cpu -mfloat-abi=soft \
            -c rostrt/atomics_ldrex.s -o rostrt/build/atomics-$tag.o
    fi
    echo "built $tag ($triple/$cpu)"
}

build_profile armv4-none-eabi strongarm110 sa
build_profile armv8a-none-eabi cortex-a72 a72
