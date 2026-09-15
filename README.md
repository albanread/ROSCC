> [!CAUTION]
> 🛑 **REPOSITORY CLOSED — UNMAINTAINED.**
>
> Do not assume the reliability of any data in this repository. It will be
> archived on **15 October 2026**.

# roscc

The RISC OS 5 back end of a modern toolchain. It takes ordinary **ELF32 ARM
EABI** object files — from any compiler that emits them — and writes RISC OS
images: Absolute applications (`,ff8`) and relocatable modules (`,ffa`),
together with the runtime those images need. A program can run against a small
freestanding runtime, or bind the ROM's own **SharedCLibrary** and get the
real C library from the OS.

```
any compiler that emits ELF32 ARM  ─►  .o ─┐
                                           ├─►  roscc link  ─►  AIF app (,ff8)
runtime: rostrt  or  roclib        ─►  .o ─┘              └──►  module (,ffa)
```

roscc is **not a compiler**. It is the part that knows what RISC OS is: image
formats, the application slot, the AIF header, filetypes, and how a program
reaches the operating system. Everything upstream of the object file can be
any toolchain at all, which is the point of it.

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
| `roscc link --module <objs...>` | link at base 0 into a relocatable module, header and veneers from the `.module` section |
| `roscc ingest <file.ll> [-o out.o]` | parse LLVM IR, retarget to RISC OS ARM, emit an object *(needs the `llvm` feature)* |
| `roscc demo` | emit Cortex-A72 smoke-test objects *(needs the `llvm` feature)* |

Two profiles: **`riscos-a72`** (Cortex-A72 in AArch32, Raspberry Pi 4, run on
the QEMU fork) and **`riscos-sa`** (StrongARM, ARMv4, the RPCEmu sandbox).
`--cpu` selects.

## Two runtimes

Every image links a runtime. There are two, and a program picks one at link
time:

**`rostrt` — freestanding.** The self-contained runtime, with no dependency on
anything already in the machine:

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

**`roclib` — the ROM's SharedCLibrary.** Binds RISC OS's own shared C library,
so a program gets the real `malloc`, `stdio`, `string`, `ctype`, `qsort`,
`time` and the rest from the operating system rather than a bundled copy. A
program's `main()` hands control to `roclib_run(real_main)`; the runtime does
the library registration, runs `_kernel_init`, and calls back into
`real_main(argc, argv)` with a working heap and the library's argv/redirection
engine behind it. See **The SharedCLibrary binding** below for what is and
is not wired up.

## The SharedCLibrary binding

`roclib` is the client side of RISC OS's shared C library — the same contract
the DDE's own programs use. The library, at registration, walks a list of
*chunk descriptors* and overwrites each of the client's one-instruction slots
with a jump into an address table it writes just past them; the client's
statics are copied into a workspace the module carves, and `_kernel_init` sets
up the stateful half. roclib provides two chunks — the kernel (48 slots) and
the C-library classics (185 slots) — with the exact statics sizes the module
checks for. The veneer tables are generated (`tools/clibspec/gen_veneers.py`
→ `rostrt/roclib.s`); the registration and `_kernel_init` handshake are in
`rostrt/crt0_clib.s` and `rostrt/roclib_init.s`.

The contract was decoded from ROOL's `RISC_OSLib` source rather than guessed;
`tools/clibspec/SOURCES.md` maps which source file owns which part. That
source is ROOL-licensed and is **not** reproduced in this repository.

**What works today**, measured on the QEMU Pi 4 fork:

- **The pure surface** — `strlen`, `strcmp`, and the other functions that
  touch no library state: 19 of them green across four machines, three runs
  each (the `clibtorture` suite).
- **The stateful core** — `malloc`, the `stdio` family, `fopen`, `qsort` and
  `bsearch` (through AAPCS comparators), `time` and `clock` — green once
  registration and `_kernel_init` have run (`clibstate`, and a whole program
  end to end in `mojopath`).

**Not yet:**

- `strtok`, the `atoi`/`strtol` locale path, and `gmtime`/`asctime` (the
  territory path) still fault through the veneer.
- `qsort` has shown an intermittent fault on the heap path after a fresh
  boot — the current top open question.
- Only the **`identical`** class is bound — scalar and pointer prototypes
  whose AAPCS (Clang) and APCS-32 (the library) layouts coincide. Float,
  variadic and struct-by-value functions stay in `rostrt`'s local
  implementations for now.

## Mojo

roscc's first front is Mojo. `tools/clibspec/gen_mojo.py` turns the binding
spec (`clib.json`) into `clib.mojo`: every `identical`-class function becomes
an `external_call` wrapper that links straight against the roclib veneer
slots. The rule the package enforces is that **Mojo code talks to the C
library ABI, not to SWIs** — a future RISC OS rebuilds the binding, not the
programs. `test/mojopath.ll` is a whole program in the shape the compiler
emits (malloc, a checksum, a clean exit), proven end to end through roclib.

## Building

roscc is Rust, and **the linker needs no LLVM**:

```bash
cargo build
```

Only `ingest` and `demo` — the LLVM-IR front — link LLVM, behind an optional
feature:

```bash
cargo build --features llvm      # needs an LLVM prefix; see .cargo/config.toml
```

The runtime objects are built separately, with Clang, for both profiles:

```bash
tools/build-rt.sh                # macOS: LLVM=/usr/local/opt/llvm@22/bin tools/build-rt.sh
```

That produces `rostrt/build/*-sa.o` and `*-a72.o`: `crt0`, the freestanding
`rostrt` and its 45 SWI shims, `aeabi`, `atomics`, and the SharedCLibrary
objects `crt0clib`, `roclib` and `roclibinit`.

## Linking a program

Freestanding:

```bash
roscc link --entry _start -o "prog,ff8" \
    rostrt/build/crt0-a72.o prog.o \
    rostrt/build/rostrt-a72.o rostrt/build/swis_os-a72.o \
    rostrt/build/aeabi-a72.o rostrt/build/atomics-a72.o
```

Against the SharedCLibrary (the program's `main()` calls `roclib_run`):

```bash
roscc link --entry _start -o "prog,ff8" \
    rostrt/build/crt0clib-a72.o prog.o \
    rostrt/build/roclib-a72.o rostrt/build/roclibinit-a72.o \
    rostrt/build/aeabi-a72.o rostrt/build/atomics-a72.o
```

The `,ff8` suffix is how the file carries its RISC OS filetype across a host
filing system.

## Testing

The programs under `test/` are the live checks: `hello`, `clibtest`, the
`clibtorture` suite (pure functions), `clibstate` (the stateful half), and
`mojopath` (the Mojo path). They run on the QEMU Pi 4 fork and, for the
StrongARM profile, on RPCEmu. `tools/clibspec/dbg.py` is a small GDB-RSP
client for the fork's emulator stubs — breakpoint, step, watchpoint — used to
decode the library's registration and `_kernel_init` chain instruction by
instruction.

## Relocations

`R_ARM_ABS32`, `CALL`, `JUMP24`, `MOVW_ABS_NC`, `MOVT_ABS`, `V4BX`, `REL32`,
`GOT_PREL`, and the static-base and PC-relative `MOVW`/`MOVT` pairs the ARMv8
runtime uses (`MOVW_BREL`/`MOVT_BREL`, `MOVW_PREL`/`MOVT_PREL`) — the ARM32
static-link core set. A GOT is synthesised for `GOT_PREL` references.

## What it does not do yet

Worth knowing before you point another compiler at it:

- **ARM mode only.** No `R_ARM_THM_*`, so no Thumb objects.
- **No archives.** The link line names its objects; there is no `.a` support
  with symbol-driven member pulling.
- **No section garbage collection**, so nothing is stripped for being unused.
- **`.bss` is materialised** as zeros in the image rather than declared in the
  AIF header's zero-init field. Now that the heap comes from the slot this
  costs a few kilobytes rather than a few hundred.
- **Parts of the SharedCLibrary surface** are not wired up yet — see the list
  above.

## Licence

MIT — see `LICENSE`. `NOTICE` records what this project does *not* include:
LLVM is linked, not distributed; the SharedCLibrary contract is read from RISC
OS Open's source, which is kept outside this repository and not reproduced
here; no RISC OS Open or Acorn source is included.
