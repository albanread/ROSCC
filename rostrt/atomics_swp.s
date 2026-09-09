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

@ ---- load, store, exchange, compare-exchange ----
@ RISC OS on a RiscPC is single-core, so a plain aligned word load or store is
@ already indivisible; the atomic forms differ only in the ordering they
@ promise, and ordering is free where nothing else executes concurrently. The
@ read-modify-write pair still needs SWP, which is the only ARMv4 primitive
@ that does it in one bus operation.
@
@ Mojo reaches these through String and List refcounting, so anything that
@ builds a String pulls __atomic_load_4 in whether the program says "atomic"
@ or not.

	.global __atomic_load_4
	.global __atomic_store_4
	.global __atomic_exchange_4
	.global __atomic_compare_exchange_4

@ (ptr r0, memorder r1) -> value
__atomic_load_4:
	ldr	r0, [r0]
	mov	pc, lr

@ (ptr r0, val r1, memorder r2)
__atomic_store_4:
	str	r1, [r0]
	mov	pc, lr

@ (ptr r0, val r1, memorder r2) -> old
__atomic_exchange_4:
	swp	r0, r1, [r0]
	mov	pc, lr

@ (ptr r0, expected* r1, desired r2, weak r3, succ, fail) -> bool
@ On mismatch the ABI requires the actual value be written back through
@ `expected`, or the caller's retry loop compares against a stale word forever.
__atomic_compare_exchange_4:
	stmfd	sp!, {r4, r5}
	ldr	r4, [r1]		@ expected value
1:	ldr	r5, [r0]		@ current
	cmp	r5, r4
	bne	2f			@ differs: report failure
	swp	r12, r2, [r0]		@ try to install desired
	cmp	r12, r4			@ did we swap the word we tested?
	bne	1b			@ no: someone moved it, retry
	mov	r0, #1
	ldmfd	sp!, {r4, r5}
	mov	pc, lr
2:	str	r5, [r1]		@ hand the actual value back
	mov	r0, #0
	ldmfd	sp!, {r4, r5}
	mov	pc, lr
