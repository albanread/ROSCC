@ crt0_clib — the C-library entry.
@
@ A program linked with this crt0 instead of the plain one enters the
@ SharedCLibrary's own flow: roclib_run registers the client, hands off
@ through _kernel_init and the RTSK Initialise, and the library's _main
@ builds argv and calls this program's `main(argc, argv)` — so malloc,
@ stdio and the whole portable surface work, and exit is library-owned.
@ The price: main's return code is the exit code, and the program is a
@ C-library client from its first instruction.
@
@ Mirrors crt0.s's stack setup (RISC OS gives an AIF image no stack;
@ OS_GetEnv's R1 is the canonical top) then hands control to roclib_run.

        .syntax unified
        .arm

        .global _start
        .extern roclib_run
        .extern rostrt_init
        .extern main

_start:
        swi     0x10                    @ OS_GetEnv: r0 -> strings, r1 = RAM limit
        mov     sp, r1                  @ establish the stack top
        bl      rostrt_init             @ rostrt's own state (argv tail, arena)
        ldr     r0, =main
        bl      roclib_run              @ never returns; the library calls main

        .pool
