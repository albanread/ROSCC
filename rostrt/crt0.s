@ rostrt crt0 — RISC OS program entry, AIF-called via the header BL.
@ r14 holds the header return address (0x8014); we never return.
@
@ RISC OS does NOT provide a stack to an Absolute/AIF image: the program
@ must set r13 itself. OS_GetEnv (SWI &10) returns the top of usable
@ memory in R1 — the canonical stack top. (Found live by the emulator
@ side's GDB stub: data abort with r13 = &80000000.)

	.syntax unified
	.code 32

	.global _start
_start:
	swi	0x10		@ OS_GetEnv: R0 -> env strings, R1 = RAM limit
	mov	sp, r1		@ establish stack at top of our memory slot
	bl	rostrt_init	@ r0 = argc (argv via rostrt_argv_ptr)
	ldr	r1, =rostrt_argv_ptr
	ldr	r1, [r1]
	bl	main
	mov	r2, r0		@ exit code
	mov	r0, #0		@ OS_Exit reason: normal
	swi	0x11

	.pool
