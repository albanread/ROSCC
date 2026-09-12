# roscc

The RISC OS 5 back end of a modern toolchain. It takes ordinary **ELF32 ARM
EABI** object files and writes RISC OS images — Absolute (`&FF8`) or
relocatable modules (`&FFA`) — together with `rostrt`, the runtime those
images need.

```
any compiler that emits ELF32 ARM  ──►  .o ──┐
                                             ├──►  roscc link  ──►  AIF image (,ff8)
rostrt: crt0, runtime, SWI shims   ──►  .o ──┘                 └──►  module (,ffa)
```

roscc is **not a compiler**. It is the part that knows what RISC OS is: image
formats, the application slot, the AIF header, filetypes. Everything upstream
of the object file can be any toolchain at all, which is the point of it.

That is not aspiration — it is what the tree already does. Every image roscc
produces links objects from *two* independent toolchains: the runtime and the
SWI shims are Clang-compiled C and assembly, and the application code comes
from a separate compiler entirely. Nothing in roscc knows which produced a
given object.

## What that buys

Porting a language to RISC OS normally means writing a RISC OS code generator.
With roscc it means **writing a runtime**, which is a far smaller surface:
teach the language how to reach the OS, then hand roscc ordinary ARM objects.
GCC, Clang, Rust, Zig, FreePascal and anything else emitting ARM EABI objects
are candidates without touching their back ends.

## Subcommands

| | |
| --- | --- |
| `roscc link [opts] <objs...>` | link at `&8000` into an AIF executable, plus an `.elf` sidecar for symbols |
| `roscc link --module <objs...>` | link at base 0 into a relocatable module, header from the `.module` section |
| `roscc ingest <file.ll> [-o out.o]` | parse LLVM IR, retarget to RISC OS ARM, emit an object |
| `roscc demo` | emit Cortex-A72 smoke-test objects |

Two profiles: **`riscos-a72`** (Cortex-A72 in AArch32, Raspberry Pi 4) and
**`riscos-sa`** (StrongARM, ARMv4, the RPCEmu sandbox). `--cpu` selects.

## rostrt — the runtime

`rostrt/` is what makes an ELF object into a RISC OS program:

- **`crt0.s`** — RISC OS gives an AIF image no stack, so the entry code sets
  `sp` itself from `OS_GetEnv`.
- **The heap comes from the application slot**, not from the image. RISC OS
  hands a program everything between the end of its image and the limit
  `OS_GetEnv` reports, so `rostrt` takes its arena and heap from there. The
  heap is therefore as large as the slot allows rather than a fixed reserve,
  and a hello world is about 9 KB rather than 300 KB of zeroes. A relocatable
  module has no slot and claims from the RMA instead: build it with
  `-DROSTRT_STATIC_HEAP`.
- **One C shim per SWI.** 45 generated modules covering the documented SWI
  set, each issuing exactly one `swi` instruction, so callers never touch a
  register. Generated from factual columns — names, numbers, register roles —
  and citing the PRM rather than reproducing it.
- **`aeabi.s`** supplies the integer division family and nothing else. There
  is no soft-float runtime: a `double` anywhere fails at the linker. Fixed
  point is the idiom, as it was on a StrongARM.

## Building

roscc is Rust and links LLVM through `llvm-sys`:

```bash
cargo build
```

`.cargo/config.toml` points `LLVM_SYS_221_PREFIX` at a local LLVM prefix and
`build.rs` copies `LLVM-C.dll` beside the executable; both currently assume a
Windows install under `C:\Program Files\LLVM`. Adjust for your machine.

The runtime objects are built separately, for both profiles:

```bash
tools/build-rt.sh
```

That produces `rostrt/build/*-sa.o` and `*-a72.o`, including one object per
SWI module.

## Linking a program

```bash
roscc link --entry _start -o "prog,ff8" \
    rostrt/build/crt0-a72.o prog.o \
    rostrt/build/rostrt-a72.o rostrt/build/swis_os-a72.o \
    rostrt/build/aeabi-a72.o rostrt/build/atomics-a72.o
```

The `,ff8` suffix is how the file carries its RISC OS filetype across a host
filing system.

## Relocations

`R_ARM_ABS32`, `CALL`, `JUMP24`, `MOVW_ABS_NC`, `MOVT_ABS`, `V4BX`, `REL32`,
`GOT_PREL` — the ARM32 static-link core set. A GOT is synthesised for
`GOT_PREL` references.

## What it does not do yet

Worth knowing before you point another compiler at it:

- **ARM mode only.** No `R_ARM_THM_*`, so no Thumb objects.
- **No archives.** The link line names its objects; there is no `.a` support
  with symbol-driven member pulling.
- **No section garbage collection**, so nothing is stripped for being unused.
- **`.bss` is materialised** as zeros in the image rather than declared in the
  AIF header's zero-init field. Now that the heap comes from the slot this
  costs a few kilobytes rather than a few hundred.
- The build assumes Windows paths, as above.

## Licence

MIT — see `LICENSE`. `NOTICE` records what this project does *not* include:
LLVM is linked, not distributed, and no RISC OS Open or Acorn source is
reproduced here.
