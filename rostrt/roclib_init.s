@ roclib_init — register this program as a SharedCLibrary client.
@
@ Two steps, exactly as the library's own cl_stub.s drives them:
@
@   1. SWI XSharedCLibrary_LibInitAPCS_32 (&80683): r0 the descriptor
@      list, r1/r2 the workspace bounds (the client's statics end and
@      the RAM limit OS_GetEnv reports), r3 = -1 (no zero-init base),
@      r4 = 0, r5 = -1 (no statics copy), r6 = stack size K<<16 | bit 0
@      (32-bit client).  The module fills the MOV pc,#0 slots with
@      veneers and returns a client word in r0.
@
@   2. The _kernel_init slot, with r0 -> _k_init_block (image RO base and
@      the RTSK block bounds) and r4 = the word LibInit returned.  This
@      is where the module builds the client's kernel state — heap,
@      handlers, stdio.  Without it, statics stay zero and malloc/fopen
@      walk into wild pointers.
@
@ The RTSK block is the run-time-system descriptor the module reads for
@ the client's language ("C") and entry points; v1 carries null handlers
@ and no ctors.  Returns the library version in r0, or 0 on failure.

        .syntax unified
        .arm

        .global roclib_init
        .extern _clib_stub_init
        .extern __image_end
        .extern _kernel_init

        .text
roclib_init:
        push    {r4, r5, r6, r7, lr}
        swi     0x10                    @ OS_GetEnv: r0 -> strings, r1 = RAM limit
        mov     r2, r1                  @ keep the RAM limit in r7's old role
        ldr     r1, =_k_data_start      @ workspace start: the statics block
        add     r2, r1, #(512 << 10)    @ workspace end: statics + 512 K
        ldr     r0, =_clib_stub_init    @ descriptor list, -1 terminated
        mov     r3, #-1
        mov     r4, #0
        mov     r5, #-1
        ldr     r6, =((512 << 16) | 1)  @ 512 K stack, 32-bit client — the
                                        @ proven-green pure registration
        swi     0xA0683                 @ X SharedCLibrary_LibInitAPCS_32
        bvs     .Lfailed

        pop     {r4, r5, r6, r7, pc}    @ r6 = version

@ roclib_init_stateful — registration, then the _kernel_init step that
@ builds the client's kernel state (heap, handlers, stdio).  Driven the
@ way k_init.s drives it: r0 the init block, r1/r2 the workspace bounds,
@ r3 = 0, r4 the RAM limit, and v6/sb (r9) pointing at the statics
@ block — the APCS client register our AAPCS code must supply.  The
@ module's veneer literal pools sit past each table end (found live:
@ see the slack in roclib.s).  FRONTIER: this step still faults inside
@ the module — the argument contract has a residue to map (see
@ test/clibstate.c).
        .global roclib_init_stateful
roclib_init_stateful:
        push    {r4, r5, r6, r7, lr}
        swi     0x10                    @ OS_GetEnv: r1 = RAM limit
        mov     r2, r1                  @ workspace end: the RAM limit
        ldr     r1, =__image_end        @ workspace start
        ldr     r0, =_clib_stub_init
        mov     r3, #-1
        mov     r4, #0
        mov     r5, #-1
        ldr     r6, =((4 << 16) | 1)    @ measured from the DDE's own crt
        mov     r7, #0                  @ zero-init size: none
        swi     0xA0683                 @ X SharedCLibrary_LibInitAPCS_32
        bvs     .Lfailed

        @ r9 stays as the SWI left it (the DDE client enters _kernel_init
        @ the same way — the module computes statics from r1/r0 itself).
        @ cl_stub's post-LibInit protocol, verbatim: r4 takes the word
        @ LibInit returned in r0, and r1/r2 keep the module's own stack
        @ bounds — it carves its layout (its bounds ran 0x2C0 below
        @ ours on the farm) and _kernel_init wants ITS numbers, not
        @ ours.  Overwriting them is what walked the garbage chain.
        mov     r4, r0
        ldr     r0, =_k_init_block      @ {RO base, RTSK base, RTSK limit}
        mov     r3, #0
        bl      _kernel_init            @ the veneered slot
        @ If the module returns rather than driving the client through
        @ _kernel_init's own path, continue to the caller with r6 intact.

        pop     {r4, r5, r6, r7, pc}    @ r6 = version

.Lfailed:
        @ r0 -> error block {number, message}: say which SWI was refused
        @ and why — a wrong chunk number and a rejected descriptor read
        @ very differently here.
        push    {r0}
        ldr     r0, =err_prefix
        swi     0x02                    @ OS_Write0
        pop     {r0}
        add     r0, r0, #4              @ the message after the number
        swi     0x02                    @ OS_Write0
        swi     0x00                    @ OS_NewLine
        mov     r0, #0
        pop     {r4, r5, r6, r7, pc}

err_prefix:
        .asciz  "roclib_init: LibInitAPCS_32 (&80683) failed: "
        .align  2

        .data
        .align  2
_k_init_block:
        .word   0x8080                  @ image RO base (entry address)
        .word   __rtsk
        .word   __rtsk_end

__rtsk:
        .word   __rtsk_end - __rtsk     @ descriptor size
        .word   0x8080, 0x8080          @ C$$code base, limit (unused)
        .word   clang_string            @ "C"
        .word   rtsk_initialise         @ the module calls this
        .word   0                       @ shared library: no Finalise
        .word   0, 0                    @ trap handlers
        .word   0, 0                    @ event handlers
        .word   0, 0, 0                 @ FastEvent, Unwind, Name
        .word   0, 0                    @ ctor bounds
        .word   0, 0                    @ dtor bounds
        .word   0, 0                    @ ctorvec bounds
        .word   0, 0                    @ dtorvec bounds
__rtsk_end:

clang_string:
        .asciz  "C"

        .text
        .align  2
rtsk_initialise:                        @ v1: nothing to register yet
        mov     r0, #0
        bx      lr

        .pool
