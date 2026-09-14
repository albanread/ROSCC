# MojoMod — a RISC OS relocatable module whose body is written in Mojo.
#
# Nothing here knows it is inside a module: these are plain C-ABI functions.
# tools/gen_module.py generates the header, the tables and the veneers that
# turn RISC OS's register-and-V-flag contract into these calls, and
# `roscc link --module` checks that the result really is position independent
# before writing it out.
#
# The runtime's writable statics — its heap, its global arena — work here,
# which is the point of the static-base model: the linker puts them in their
# own address space and initialisation claims it from the RMA. What does not
# work yet is Mojo's `print()` with formatted arguments; a bare literal is
# fine. See docs/MODULES.md. Output here goes through OS_WriteC and OS_Write0,
# which is what a module would reach for anyway.
#
# What a module body still has to respect:
#   - state that must survive a call belongs in the module's own area, which
#     arrives here as `ws`. (Ordinary Mojo globals now work too — they land in
#     the same claimed area — but the explicit pointer says what it means.)
#   - shallow stacks: this runs on the SVC stack, not an application's.
#   - r9 is the static base for the whole call graph, which is why the build
#     passes `--target-features +reserve-r9`.

from riscos import os
from std.ffi import external_call

comptime Word = UnsafePointer[Int32, MutUntrackedOrigin]

# Module area layout. A module's private word is one word wide, so everything
# it remembers is reached through it — here, two counters.
comptime WS_COMMANDS = 0
comptime WS_SWIS = 1


fn write_int(v: Int32):
    """Decimal, one digit at a time through OS_WriteC. Ten frames deep at
    most, which matters: this runs on the SVC stack."""
    if v < 0:
        os.write_c(45)  # '-'
        write_int(-v)
        return
    if v >= 10:
        write_int(v // 10)
    os.write_c(48 + (v % 10))


fn fib(n: Int32) -> Int32:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


# ---------------- module lifetime ----------------

@export
fn mojomod_init(ws: Word) abi("C") -> Int32:
    """Called on load, and again after an RMA tidy. The area has just been
    claimed and zeroed, and the static base is established."""
    ws[unsafe_offset=WS_COMMANDS] = 0
    ws[unsafe_offset=WS_SWIS] = 0
    os.write0("MojoMod: initialised, and this line was printed by Mojo\r\n")
    return 0  # 0 = no error; anything else is a RISC OS error block pointer


@export
fn mojomod_final(fatal: Int32, ws: Word) abi("C") -> Int32:
    os.write0("MojoMod: finalised after ")
    write_int(ws[unsafe_offset=WS_COMMANDS])
    os.write0(" command(s) and ")
    write_int(ws[unsafe_offset=WS_SWIS])
    os.write0(" SWI(s)\r\n")
    return 0


# ---------------- * command ----------------

@export
fn mojomod_command(tail: Int32, argc: Int32, ws: Word) abi("C") -> Int32:
    ws[unsafe_offset=WS_COMMANDS] += 1

    os.write0("MojoMod 1.00 - Mojo code running inside a RISC OS module\r\n")

    var total: Int32 = 0
    for i in range(1, 21):
        total += fib(Int32(i))
    os.write0("  fib(1..20) = ")
    write_int(total)
    os.write0(" (sum)\r\n")

    os.write0("  called ")
    write_int(ws[unsafe_offset=WS_COMMANDS])
    os.write0(" time(s), argc = ")
    write_int(argc)
    os.write0("\r\n")
    return 0


# ---------------- SWIs ----------------
#
# A SWI handler is handed the caller's R0-R9 as a block it may read and
# write; whatever it leaves there is what the caller gets back. That is the
# whole reason these take a pointer rather than arguments.

@export
fn mojomod_swi_add(regs: Word, ws: Word) abi("C") -> Int32:
    """MojoMod_Add: R0 + R1 -> R0."""
    ws[unsafe_offset=WS_SWIS] += 1
    regs[unsafe_offset=0] = regs[unsafe_offset=0] + regs[unsafe_offset=1]
    return 0


@export
fn mojomod_swi_counter(regs: Word, ws: Word) abi("C") -> Int32:
    """MojoMod_Counter: R0 = commands run, R1 = SWIs served, and — as a check
    on the static-base model itself — R2 = a C .bss counter and R3 a C .data
    value that only survives if the linker carried it and init copied it."""
    ws[unsafe_offset=WS_SWIS] += 1
    regs[unsafe_offset=0] = ws[unsafe_offset=WS_COMMANDS]
    regs[unsafe_offset=1] = ws[unsafe_offset=WS_SWIS]
    regs[unsafe_offset=2] = external_call["rwpi_check", Int32](regs + 3)
    return 0
