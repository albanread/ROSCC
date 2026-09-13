@ roclib_init — register this program as a SharedCLibrary client.
@
@ The handshake (from the library's own cl_stub.s, adapted): with r0 the
@ descriptor list, r1/r2 the workspace bounds (the client's statics end and
@ the RAM limit OS_GetEnv reports), r3 = -1 (no zero-init base), r4 = 0,
@ r5 = -1 (no statics copy), and r6 = stack size in K<<16 | bit 0 (a
@ 32-bit client), SWI XSharedCLibrary_LibInitAPCS_32 (&80683) fills the
@ MOV pc,#0 slots in Stub$$Entries with veneers.  Returns the library
@ version in r0, or 0 if the call failed (V set).
@
@ The library owns the workspace between our statics and the stack — the
@ heap it hands to malloc comes from there, so a program that also uses
@ rostrt's own heap must keep the two apart.

        .syntax unified
        .arm

        .global roclib_init
        .extern _clib_stub_init
        .extern __image_end

        .text
roclib_init:
        push    {r4, r5, r6, r7, lr}
        swi     0x10                    @ OS_GetEnv: r0 -> strings, r1 = RAM limit
        mov     r2, r1                  @ workspace end
        ldr     r1, =__image_end        @ workspace start: end of our statics
        ldr     r0, =_clib_stub_init    @ descriptor list, -1 terminated
        mov     r3, #-1
        mov     r4, #0
        mov     r5, #-1
        ldr     r6, =((512 << 16) | 1)  @ 512 K stack, 32-bit client
        swi     0xA0683                 @ X SharedCLibrary_LibInitAPCS_32
        popvc   {r4, r5, r6, r7, pc}    @ (r6 = version)

        @ Failure: r0 -> error block {number, message}.  Say which SWI was
        @ refused and why — a wrong chunk number and a rejected descriptor
        @ read very differently here.
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

        .pool
