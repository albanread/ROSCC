@ rostrt — 32-bit atomic fetch_sub for ARMv4/StrongARM using SWP.
@ GCC/LLVM __atomic builtin ABI: (ptr r0, val r1, memorder r2) -> old.

	.syntax unified
	.cpu strongarm110
	.code 32

	.global __atomic_fetch_sub_4
	.global __atomic_fetch_add_4

__atomic_fetch_sub_4:
	stmfd	sp!, {r4, r5}
	mov	r4, r0
	mov	r5, r1
1:	ldr	r0, [r4]	@ expected old
	sub	r3, r0, r5	@ new value
	swp	r12, r3, [r4]	@ r12 = actual old, *ptr = new
	cmp	r12, r0
	bne	1b		@ lost the race: retry with actual
	mov	r0, r12
	ldmfd	sp!, {r4, r5}
	mov	pc, lr

__atomic_fetch_add_4:
	stmfd	sp!, {r4, r5}
	mov	r4, r0
	mov	r5, r1
1:	ldr	r0, [r4]
	add	r3, r0, r5
	swp	r12, r3, [r4]
	cmp	r12, r0
	bne	1b
	mov	r0, r12
	ldmfd	sp!, {r4, r5}
	mov	pc, lr


	.global __sync_synchronize
__sync_synchronize:
	mov	pc, lr		@ full barrier: single-core, IRQ-driven RISC OS
