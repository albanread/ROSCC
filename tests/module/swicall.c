/* swicall shims — calling MojoMod's SWIs from outside the module.
 *
 * A SWI number is an immediate field in the instruction, so calling one
 * needs a literal; that is why these live in C beside the Mojo application
 * rather than in it. The X form (bit 17 set, &E7000 rather than &C7000) is
 * used throughout: it returns errors in V instead of handing them to the
 * system error handler, so a missing module is something this program can
 * report rather than something that kills it.
 */

/* MojoMod_Add (&C7000): R0 + R1 -> R0. */
int mojomod_add(int a, int b, int *failed)
{
    register int r0 __asm("r0") = a;
    register int r1 __asm("r1") = b;
    register unsigned flags __asm("r2");
    __asm__ volatile("swi 0xE7000\n\tmrs %1, cpsr"
                     : "+r"(r0), "=r"(flags)
                     : "r"(r1)
                     : "r3", "r12", "lr", "memory");
    if (failed)
        *failed = (flags >> 28) & 1;   /* V: the module said no */
    return r0;
}

/* MojoMod_Counter (&C7001): R0 = commands run, R1 = SWIs served. */
int mojomod_counter(int *swis, int *failed, int *statics)
{
    register int r0 __asm("r0");
    register int r1 __asm("r1");
    register int r2 __asm("r2");
    register int r3 __asm("r3");
    /* Pin the flags too. Left to choose, the compiler puts them in a
     * register another operand is already pinned to, and then silently
     * drops whichever argument was in the way — two operands sharing one
     * register is a mistake it does not report. */
    register unsigned flags __asm("r12");
    __asm__ volatile("swi 0xE7001\n\tmrs %4, cpsr"
                     : "=r"(r0), "=r"(r1), "=r"(r2), "=r"(r3), "=r"(flags)
                     :
                     : "lr", "memory");
    if (failed)
        *failed = (flags >> 28) & 1;
    if (swis)
        *swis = r1;
    if (statics) {
        statics[0] = r2;   /* the module's .bss counter */
        statics[1] = r3;   /* the module's .data seed */
    }
    return r0;
}

/* An out-of-range SWI in the module's own chunk: the handler's bounds check
 * should turn this into an error, not a jump into the branch table. */
int mojomod_bad_swi(int *failed)
{
    register int r0 __asm("r0");
    register unsigned flags __asm("r1");
    __asm__ volatile("swi 0xE703F\n\tmrs %1, cpsr"
                     : "=r"(r0), "=r"(flags)
                     :
                     : "r2", "r3", "r12", "lr", "memory");
    if (failed)
        *failed = (flags >> 28) & 1;
    return r0;
}

/* Formatting, kept here so the test program depends on nothing but SWIs.
 * The application runtime can print(), but that pulls in the KGEN globals
 * machinery, and this program is about the module, not about the runtime. */

static void write0(const char *s)
{
    register const char *r0 __asm("r0") = s;
    __asm__ volatile("swi 0x02" : : "r"(r0) : "r1", "r2", "r3", "r12", "lr", "memory");
}

void swicall_write_int(int v)
{
    char buf[12];
    int i = (int) sizeof buf;
    unsigned u = (v < 0) ? (unsigned) (-v) : (unsigned) v;

    buf[--i] = 0;
    do {
        buf[--i] = (char) ('0' + (u % 10u));
        u /= 10u;
    } while (u);
    if (v < 0)
        buf[--i] = '-';
    write0(&buf[i]);
}

void swicall_write_hex(unsigned v)
{
    static const char digits[] = "0123456789ABCDEF";
    char buf[9];
    int i = 8;

    buf[i] = 0;
    do {
        buf[--i] = digits[v & 0xf];
        v >>= 4;
    } while (v);
    write0(&buf[i]);
}

/* OS_SWINumberFromString, X form (&20039).
 *
 * The generated shims in rostrt use the plain form, which hands an error to
 * the system error handler and kills the program. That is the right default
 * for a program that cannot continue, and the wrong one here: "is that SWI
 * name known?" is the question this test is asking, and both answers are
 * results. Returns 0 and sets *failed when the name is not known.
 */
int swicall_swi_number(const char *name, int *failed)
{
    register const char *r1 __asm("r1") = name;
    register int r0 __asm("r0");
    register unsigned flags __asm("r2");
    __asm__ volatile("swi 0x20039\n\tmrs %1, cpsr"
                     : "=r"(r0), "=r"(flags)
                     : "r"(r1)
                     : "r3", "r12", "lr", "memory");
    if (failed)
        *failed = (flags >> 28) & 1;
    return (flags & (1u << 28)) ? 0 : r0;
}
