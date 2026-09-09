@ rostrt — 32-bit atomic fetch_add/sub for ARMv7+ using LDREX/STREX.
@ GCC/LLVM __atomic builtin ABI: (ptr r0, val r1, memorder r2) -> old.

	.syntax unified
	.cpu cortex-a72
	.code 32

	.global __atomic_fetch_sub_4
	.global __atomic_fetch_add_4

__atomic_fetch_sub_4:
1:	ldrex	r2, [r0]
	sub	r3, r2, r1
	strex	r12, r3, [r0]
	cmp	r12, #0
	bne	1b
	mov	r0, r2
	bx	lr

__atomic_fetch_add_4:
1:	ldrex	r2, [r0]
	add	r3, r2, r1
	strex	r12, r3, [r0]
	cmp	r12, #0
	bne	1b
	mov	r0, r2
	bx	lr


	.global __sync_synchronize
__sync_synchronize:
	dmb	ish		@ full barrier
