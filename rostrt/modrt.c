/* modrt — the runtime a RISC OS module may link against.
 *
 * rostrt.c is for an application: it owns a static argv buffer, a static
 * arena for KGEN_CompilerRT globals, and a stack it sets up itself. None of
 * that is legal in a module — the RMA copy is shared by every instantiation,
 * a ROM module cannot be written at all, and the code runs wherever the RMA
 * had room, so a literal pointing at a static would point somewhere else
 * entirely.
 *
 * So a module links this instead: SWI veneers with no state of their own,
 * plus the two calls that get a module the RAM it is allowed to have.
 * Anything the module wants to remember goes in workspace claimed here and
 * kept in the private word.
 *
 * Build with -fropi so string literals are reached PC-relative; roscc
 * refuses the module otherwise.
 *
 * Every shim below clobbers "lr". A module runs in SVC mode, and SWI banks
 * its return address into r14_svc — the same register a leaf function was
 * about to return through. Without the clobber the compiler keeps the
 * return address in lr, the SWI overwrites it, and `mov pc, lr` branches to
 * itself: a one-instruction infinite loop that only shows up inside a
 * module, never in an application.
 */

typedef unsigned int u32;

/* The name riscos.os.write0() binds to (rostrt.c has its own copy for
 * applications; a module links exactly one of the two). */
__attribute__((noinline)) void os_write0(const char *s)
{
    register const char *r0 __asm("r0") = s;
    __asm__ volatile("swi 0x02" : : "r"(r0) : "r1", "r2", "r3", "r12", "lr", "memory");
}

__attribute__((noinline)) void os_writec(int c)
{
    register int r0 __asm("r0") = c;
    __asm__ volatile("swi 0x00" : "+r"(r0) : : "r1", "r2", "r3", "r12", "lr", "memory");
}

/* OS_NewLine (SWI &3). */
__attribute__((noinline)) void os_newline(void)
{
    __asm__ volatile("swi 0x03" : : : "r0", "r1", "r2", "r3", "r12", "lr", "memory");
}

/* OS_Module 6 (Claim) — RMA workspace for this instantiation. X form: a
 * module must return an error, never take one. Returns 0 if the claim
 * failed. */
__attribute__((noinline)) void *mod_claim(u32 size)
{
    register u32 r0 __asm("r0") = 6;
    register u32 r3 __asm("r3") = size;
    register void *r2 __asm("r2");
    register u32 flags __asm("r1");
    __asm__ volatile("swi 0x2001E\n\tmrs %1, cpsr"
                     : "=r"(r2), "=r"(flags)
                     : "r"(r0), "r"(r3)
                     : "r12", "lr", "memory");
    return (flags & (1u << 28)) ? 0 : r2;
}

/* OS_Module 7 (Free). */
__attribute__((noinline)) void mod_free(void *block)
{
    register u32 r0 __asm("r0") = 7;
    register void *r2 __asm("r2") = block;
    __asm__ volatile("swi 0x2001E" : : "r"(r0), "r"(r2)
                     : "r1", "r3", "r12", "lr", "memory");
}

/* Decimal output without a static buffer: the digits live on the stack,
 * which in a module is the SVC stack — small, so keep frames tiny. */
__attribute__((noinline)) void mod_write_int(int v)
{
    char buf[12];
    int i = (int)sizeof buf;
    unsigned u = (v < 0) ? (unsigned)(-v) : (unsigned)v;

    buf[--i] = 0;
    do {
        buf[--i] = (char)('0' + (u % 10u));
        u /= 10u;
    } while (u);
    if (v < 0)
        buf[--i] = '-';
    os_write0(&buf[i]);
}
