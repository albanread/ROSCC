# The SharedCLibrary contract, read from source

Where the sources live and what each part owns.  The ROM source tarball
(ROS_PRIVATE/src) ships RISC_OSLib's assembly but not its C bodies; the
full component comes from ROOL's GitLab (`RiscOS/Sources/Lib/RISC_OSLib`,
anonymous archive download works where the web UI is bot-filtered) and
sits, ROOL-licensed, outside this public repo at
`../../gccsdk-macos/rool-src/RISC_OSLib/`.

## The map

| File | Owns |
| --- | --- |
| `s/initmodule` | **The module side of LibInit** — `_Shared_Lib_Module_SWI_Code`. Chunk walking, the statics copy, the SL machinery, veneer writing. Everything the client contract answers to. |
| `s/h_stubs` (ROM tarball) | The client-side `Entry`/`Variable` macros: one `MOV pc,#0` word per entry. |
| `s/cl_stub` | The real client crt: registration SWI + the hand-off to `_kernel_init`, then `_main`. |
| `c/armsys` | `_armsys_lib_init` (the stateful half: `_ctype_init`, `_init_alloc`, `_initio`, signals, ctors) and `_main(s, main)` — the library's argv/redirection engine that finally calls `main`. |
| `s/cl_init`, `kernel/s/k_init` | The RTSK block and the static-library variant of the kernel init. |

## The module side, decoded from `initmodule`

* Every chunk descriptor is `{id, entries start, entries end, data start,
  data end}`; the walk ends at a negative id.
* **Veneers**: every slot in a chunk is overwritten with the same
  instruction, `LDR pc,[pc,#size-8]`, where `size` is the CHUNK table's
  byte size — so every entry jumps through an **address-constant table
  written immediately after the chunk's last slot** (`r3 = slots_end`,
  constants from there).  This is why tables need slack after them (our
  roclib.s emits table-sized slack; source confirms it must be ≥ size).
* `PickRoutineVariant` (address LSB set) selects per-calling-convention
  routine variants — the APCS-A/R/32 adaptation gerph described.
* **E_StaticSizeWrong**: the client's chunk data-area size must equal the
  module's statics size for that chunk, exactly — our `&31C`/`&B48`
  blocks answer to this check.
* The client's statics described by `{data start, data end}` are COPIED
  to the workspace start (r1), the zero-init tail (r3; -1 = none
  meaningful) zeroed, and `SC_SLOffset+SL_Client_Offset` is written at
  `r12 = r1 + copied_statics_size` — the stack-chunk header.  Returned
  r1/r2 are the stack bounds the module carved.
* The registration's `r6` stack size is decoded `K<<16` (bytes→K, default
  `OldRootStackSize` when zero); the DDE crt passes 4 K.

## The client flow, complete

1. `crt0` sets sp from `OS_GetEnv`, then `cl_stub`'s init code:
   `r0 = Stub$$Init`, `r1 = Image$$RW$$Limit`, `r2 = RAM limit`,
   `r3 = -1`, `r4 = 0`, `r5 = -1`, `r6 = stack K<<16 | 1`,
   **and `r7` carries the AIF zero-init size — the module reads it**
   (measured on the farm: the DDE passes 0xD14).
2. SWI `XLibInitAPCS_32` (&A0683): patches slots, copies statics, carves
   the stack, returns bounds in r1/r2 and a client word in r0.
3. Branch to the `_kernel_init` slot with `r0 = k_init_block`
   {RO base, RTSK base, RTSK limit}, `r4 = the client word`, keeping the
   module's r1/r2.
4. The module-side kernel init runs `_armsys_lib_init` (ctype, allocator,
   stdio, signals) and then **`_main(command_string, main)`** — the
   library builds argv, handles `<`/`>` redirection, calls `main`, and
   owns exit.  A foreign client that skips `_main` skips the stateful
   half: exactly the boundary the torture suite measured
   (`__ctype` empty, malloc/stdio faulting).

## What this means for roclib

The stateful path is not a register puzzle but a flow puzzle: after
`_kernel_init` the client should hand control to `_main` (or call the
individual `_armsys_lib_init`-era initials through their slots) rather
than calling `main` itself.  Our remaining fault at the module's statics
writer is inside the chunk/statics machinery above — the next probe
reads `initmodule`'s compiled form against the trace, not the reverse.
