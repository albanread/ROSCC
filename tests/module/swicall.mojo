# swicall — an ordinary RISC OS application that calls MojoMod's SWIs.
#
# This is the outside half of the SWI test. The module offers its own SWIs;
# this program, linked as a normal Absolute image by the same toolchain,
# calls them the way any other program would — by name through the kernel's
# decoding, and by number through the kernel's SWI dispatcher. Nothing here
# knows anything about the module beyond its published interface.

from riscos import os
from std.ffi import external_call
from std.memory import stack_allocation


fn print_int(v: Int32):
    _ = external_call["swicall_write_int", Int32](v)


fn main():
    os.write0("swicall: calling a Mojo module's SWIs from outside it\n")

    var failed = stack_allocation[1, DType.int32, 4]()
    failed[unsafe_offset=0] = 0

    # 1. Name to number, which only works if the module's SWI decoding
    #    table is present and the kernel walked it.
    var number = external_call["swicall_swi_number", Int32](
        "MojoMod_Add".ptr(), failed
    )
    if failed[unsafe_offset=0] != 0:
        os.write0("  MojoMod_Add is not a known SWI name: no module?\n")
        return
    os.write0("  OS_SWINumberFromString(\"MojoMod_Add\") = &")
    _ = external_call["swicall_write_hex", Int32](number)
    os.write0("\n")

    # 2. The SWI itself, through the kernel dispatcher into the module's
    #    branch table and out into Mojo.
    var sum = external_call["mojomod_add", Int32](Int32(20), Int32(22), failed)
    if failed[unsafe_offset=0] != 0:
        os.write0("  MojoMod_Add failed: is the module loaded?\n")
        return
    os.write0("  MojoMod_Add(20, 22) = ")
    print_int(sum)
    os.write0("\n")

    # 3. State that outlived the call, held in the module's workspace.
    var swis = stack_allocation[1, DType.int32, 4]()
    swis[unsafe_offset=0] = 0
    var extra = stack_allocation[2, DType.int32, 4]()
    extra[unsafe_offset=0] = 0
    extra[unsafe_offset=1] = 0
    var commands = external_call["mojomod_counter", Int32](swis, failed, extra)
    os.write0("  MojoMod_Counter: ")
    print_int(commands)
    os.write0(" command(s), ")
    print_int(swis[unsafe_offset=0])
    os.write0(" SWI(s) so far\n")

    # The module's own report on the static-base model: a .bss counter that
    # only exists because initialisation claimed and zeroed an area, and a
    # .data value that only survives if the linker carried it and
    # initialisation copied it in.
    os.write0("  static base: .bss counter = ")
    print_int(extra[unsafe_offset=0])
    os.write0(", .data seed = &")
    _ = external_call["swicall_write_hex", Int32](extra[unsafe_offset=1])
    os.write0("\n")

    # 4. A SWI the module does not implement, to prove the bounds check in
    #    the dispatcher is doing its job.
    _ = external_call["mojomod_bad_swi", Int32](failed)
    if failed[unsafe_offset=0] != 0:
        os.write0("  unimplemented SWI refused, as it should be\n")
    else:
        os.write0("  PROBLEM: unimplemented SWI was accepted\n")
