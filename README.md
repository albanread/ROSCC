# roscc

The RISC OS 5 back end of a toolchain. It takes ELF32 ARM EABI object
files from any compiler that emits them and writes RISC OS images —
Absolute applications (,ff8) and relocatable modules (,ffa) — together
with the runtime those images need: the freestanding `rostrt`, or a
binding to the ROM's SharedCLibrary. Target: Raspberry Pi 4, Cortex-A72,
AArch32, running on the QEMU fork. Experimental software, unsupported.

## Build

Needs a Rust toolchain:

    cargo build --release

## Run

Link an AIF application at &8000:

    target/release/roscc link -o Hello,ff8 crt0.o hello.o rostrt.o swis_os.o aeabi.o

Link a relocatable module — the module header comes from
`tools/gen_module.py`:

    python3 tools/gen_module.py --title MyMod --rwpi --arch armv8a -o head.s
    clang --target=armv8a-none-eabi -mcpu=cortex-a72 -mfloat-abi=soft -c head.s -o head.o
    target/release/roscc link --module -o MyMod,ffa head.o body.o rostrt.o swis_os.o modrt.o aeabi.o atomics.o

`roscc ingest <file.ll>` parses LLVM IR and emits an ARM object (needs
the `llvm` feature).

Licence: MIT — see LICENSE and NOTICE.

This repository is scheduled to be archived. Pull requests and issues are not accepted.
