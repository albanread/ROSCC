@ roclib — register this program as a SharedCLibrary client.
@
@ The flow, read from the library's own sources (see
@ tools/clibspec/SOURCES.md): cl_stub registers with r0 the descriptor
@ list, r1 the client's statics end, r2 the RAM limit, r3 = -1, r4 = 0,
@ r5 = -1, r6 = stack size K<<16 | 1 (the DDE crt claims 4 K), r7 the
@ AIF zero-init size; SWI XLibInitAPCS_32 (&A0683) patches the slots and
@ carves the client workspace; then the _kernel_init slot with r0 the
@ init block and r4 the client word, keeping the module's returned r1/r2.
@ The module calls the RTSK's Initialise with sl set — cl_init's version
@ pokes the slot-extension byte, calls _clib_initialise, and returns the
@ address of its RunSubMain; the module calls that after _kernel_init,
@ and RunSubMain calls _kernel_command_string then _main, which builds
@ argv, runs every stateful initial, calls main, and owns exit.
@
@ roclib_init is the pure registration (proven green); roclib_run is the
@ complete flow and never returns.

        .syntax unified
        .arm

        .global roclib_init
        .global roclib_run
        .extern _clib_stub_init
        .extern __image_end
        .extern _kernel_init
        .extern _clib_initialise
        .extern _kernel_command_string
        .extern _main
        .extern _k_data_start

.set SL_Client_Offset, -536           @ s/h_stack

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

@ roclib_run(r0 = client main) — the complete flow; never returns.
roclib_run:
        push    {r4, r5, r6, r7, lr}
        ldr     r3, =roclib_client_main
        str     r0, [r3]
        swi     0x10                    @ r0 -> command string, r1 = RAM limit
        mov     r8, r1                  @ RAM limit
        ldr     r1, =__image_end        @ workspace start: end of everything
        mov     r2, r8                  @ workspace end: the RAM limit
        ldr     r0, =_clib_stub_init
        mov     r3, #-1
        mov     r4, #0
        mov     r5, #-1
        ldr     r6, =((4 << 16) | 1)    @ 4 K stack | 32-bit (DDE-measured)
        mov     r7, #0                  @ zero-init size: none
        swi     0xA0683                 @ X SharedCLibrary_LibInitAPCS_32
        bvs     .Lfailed

        mov     r4, r0                  @ the client word
        ldr     r0, =_k_init_block      @ {RO base, RTSK base, RTSK limit}
        mov     r3, #0
        bl      _kernel_init            @ the module drives the rest from here

        @ If the module returns without having called our RunSub, hang
        @ loudly rather than fall into an uninitialised exit.
.Lhang: b       .Lhang

@ The RTSK Initialise, mirroring cl_init's: called by the module with
@ a1 = the language block and sl = the client stack-chunk base.
rtsk_initialise:
        push    {r0, lr}
        @ Enable Wimp slot extension for _kernel_alloc: the byte at
        @ StaticData+0x115, relocated by the client's SL offset.
        ldr     r3, =_k_data_start
        ldr     r2, [r10, #SL_Client_Offset]
        add     r3, r3, r2
        add     r3, r3, #0x100         @ 0x115 in two steps: not an
        add     r3, r3, #0x15          @ encodable ARM immediate
        mov     r2, #1
        strb    r2, [r3]
        bl      _clib_initialise        @ a1 = the language block, as passed
        ldr     r0, =roclib_runsub      @ what we return for the module to call
        pop     {r1, pc}

@ RunSubMain: the library builds argv, runs the initials, calls the
@ client's main and owns exit.
roclib_runsub:
        push    {lr}
        bl      _kernel_command_string  @ r0 = the command tail
        ldr     r1, =roclib_client_main
        ldr     r1, [r1]
        bl      _main                   @ never returns
        pop     {pc}

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
        .asciz  "roclib: LibInitAPCS_32 (&80683) failed: "
        .align  2

        .data
        .align  2
        .global roclib_client_main
roclib_client_main:
        .word   0

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

        .pool
