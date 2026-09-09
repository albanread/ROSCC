@ rostrt — ARM ABI division helpers (ARMv4-safe, no SDIV).
@ LLVM calls these for variable division on the riscos-sa profile.
@ Pure assembly: no division instruction, no compiler runtime dependency.
@ AAPCS: __aeabi_{u,}div return quotient in r0;
@ __aeabi_{u,}divmod return {quot, rem} (64-bit: r0,r1 / r2,r3).

	.syntax unified
	.cpu strongarm110
	.code 32

	.global __aeabi_uidiv
	.global __aeabi_idiv
	.global __aeabi_uidivmod
	.global __aeabi_idivmod
	.global __aeabi_uldivmod
	.global __aeabi_ldivmod
	.global __divdi3
	.global __moddi3
	.global __udivdi3
	.global __umoddi3

@ ---- local core: r0/r1 -> quot r0, rem r1 ----
@ 32-bit restoring division, fixed 32 iterations.
uidiv_core:
	mov	r2, #0		@ remainder
	mov	r3, #0		@ quotient
	mov	r12, #32
1:	movs	r0, r0, lsl #1	@ C = top bit of numerator
	adc	r2, r2, r2	@ rem = rem*2 + C
	mov	r3, r3, lsl #1	@ quot <<= 1
	cmp	r2, r1
	blo	2f
	sub	r2, r2, r1
	orr	r3, r3, #1
2:	subs	r12, r12, #1
	bne	1b
	mov	r0, r3
	mov	r1, r2
	mov	pc, lr

__aeabi_uidiv:
	b	uidiv_core

__aeabi_uidivmod:
	b	uidiv_core

__aeabi_idiv:
	eor	r2, r0, r1	@ r2 bit31 = sign of quotient
	tst	r0, #0x80000000
	rsbmi	r0, r0, #0
	tst	r1, #0x80000000
	rsbmi	r1, r1, #0
	stmfd	sp!, {r2, lr}
	bl	uidiv_core
	ldmfd	sp!, {r2, lr}
	tst	r2, #0x80000000
	rsbmi	r0, r0, #0
	mov	pc, lr

__aeabi_idivmod:
	eor	r2, r0, r1		@ quotient sign
	and	r3, r0, #0x80000000	@ ORIGINAL dividend sign bit
	tst	r0, #0x80000000
	rsbmi	r0, r0, #0
	stmfd	sp!, {r2, r3, lr}
	tst	r1, #0x80000000
	rsbmi	r1, r1, #0
	bl	uidiv_core		@ r0 quot, r1 rem
	ldmfd	sp!, {r2, r3, lr}
	tst	r2, #0x80000000
	rsbmi	r0, r0, #0		@ fix quotient sign
	tst	r3, #0x80000000
	rsbmi	r1, r1, #0		@ remainder takes dividend's sign
	mov	pc, lr

@ ---- 64-bit unsigned core: n = r0,r1; d = r2,r3 ----
@ returns quot r0,r1; rem r2,r3. Clobbers r4-r9, r12.
uldiv_core:
	stmfd	sp!, {r4, r5, r6, r7, r8, r9, lr}
	mov	r4, #0		@ rem lo
	mov	r5, #0		@ rem hi
	mov	r6, #0		@ quot lo
	mov	r7, #0		@ quot hi
	mov	r8, r0		@ n lo
	mov	r9, r1		@ n hi
	mov	r12, #64
1:	movs	r4, r4, lsl #1	@ C = rem.lo msb
	adc	r5, r5, r5	@ rem <<= 1
	mov	lr, r9, lsr #31	@ top bit of n
	movs	r8, r8, lsl #1
	adc	r9, r9, r9	@ n <<= 1
	orr	r4, r4, lr	@ rem.lo |= old n top bit
	movs	r6, r6, lsl #1
	adc	r7, r7, r7	@ quot <<= 1
	cmp	r5, r3		@ rem >= d (64-bit compare)?
	bhi	2f
	blo	3f
	cmp	r4, r2
	bhi	2f
3:	subs	r4, r4, r2
	sbcs	r5, r5, r3	@ rem -= d
	orr	r6, r6, #1	@ quot |= 1
2:	subs	r12, r12, #1
	bne	1b
	mov	r0, r6
	mov	r1, r7
	mov	r2, r4
	mov	r3, r5
	ldmfd	sp!, {r4, r5, r6, r7, r8, r9, pc}

__aeabi_uldivmod:
	b	uldiv_core

@ negate 64-bit pair (lo,hi) in place; clobbers C only
__aeabi_ldivmod:
	stmfd	sp!, {r4, r5, lr}
	eor	r4, r0, r2	@ bit31 = quotient sign
	mov	r5, r0		@ ORIGINAL dividend lo (for remainder sign)
	tst	r1, #0x80000000	@ dividend negative?
	rsbmi	r0, r0, #0
	rscmi	r1, r1, #0
	tst	r3, #0x80000000	@ divisor negative?
	rsbmi	r2, r2, #0
	rscmi	r3, r3, #0
	bl	uldiv_core	@ r0,r1 quot; r2,r3 rem
	tst	r4, #0x80000000
	rsbmi	r0, r0, #0
	rscmi	r1, r1, #0
	tst	r5, #0x80000000	@ remainder takes dividend's sign
	rsbmi	r2, r2, #0
	rscmi	r3, r3, #0
	ldmfd	sp!, {r4, r5, pc}

@ ---- compiler-rt spellings of the same four operations ----
@ LLVM does not always emit the AEABI names. Anything that divides or prints
@ an Int64 - and Mojo's Int is 64-bit, so that is any Int64 at all reaching
@ String() - comes out as __moddi3/__divdi3 instead, and the link then fails
@ on a symbol the runtime already implements under another name.
@ __aeabi_{u,}ldivmod leaves quotient in r0,r1 and remainder in r2,r3, so the
@ quotient forms are a plain tail call and the remainder forms just move the
@ pair down.

__divdi3:
	b	__aeabi_ldivmod		@ quotient already in r0,r1

__udivdi3:
	b	uldiv_core		@ quotient already in r0,r1

__moddi3:
	stmfd	sp!, {lr}
	bl	__aeabi_ldivmod
	mov	r0, r2			@ remainder lo
	mov	r1, r3			@ remainder hi
	ldmfd	sp!, {pc}

__umoddi3:
	stmfd	sp!, {lr}
	bl	uldiv_core
	mov	r0, r2
	mov	r1, r3
	ldmfd	sp!, {pc}
