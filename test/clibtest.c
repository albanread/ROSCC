/* clibtest — the SharedCLibrary binding's first light.
 *
 * Registers the program with the SharedCLibrary (LibInitAPCS_32), then
 * calls through real slots: pure functions (strlen, strcmp) and stdio
 * (fopen/fseek/ftell against this file on the HostFS share).  Everything
 * prints with OS_Write0 and a local hex formatter: no variadic function
 * is called anywhere in this test — that class is not routed through.
 */

extern void OS_Write0(const char *);

/* The binding: slot symbols, declared exactly as the DDE headers spell
 * them (scalar prototypes only). */
extern unsigned long roclib_init(void);
extern int strlen(const char *);
extern int strcmp(const char *, const char *);
extern void *fopen(const char *, const char *);
extern int fclose(void *);
extern int fseek(void *, long, int);
extern long ftell(void *);

static int fails;

static void print_hex(unsigned long v)
{
    char buf[11];
    char *p = buf + 10;
    *p = 0;
    do {
        *--p = "0123456789ABCDEF"[v & 15];
        v >>= 4;
    } while (v);
    OS_Write0(p);
}

static void check(const char *what, unsigned long got, unsigned long want)
{
    OS_Write0(what);
    OS_Write0(": got ");
    print_hex(got);
    OS_Write0(" want ");
    print_hex(want);
    if (got == want) {
        OS_Write0("  ok\n");
    } else {
        fails++;
        OS_Write0("  FAIL\n");
    }
}

int main(void)
{
    static const char msg[] = "Hello, SharedCLibrary!";

    unsigned long ver = roclib_init();
    if (ver == 0) {
        OS_Write0("clibtest: LibInitAPCS_32 failed\n");
        return 1;
    }
    OS_Write0("clibtest: registered, CLib version ");
    print_hex(ver);
    OS_Write0("\n");

    check("strlen", strlen(msg), sizeof(msg) - 1);
    check("strcmp(eq)", strcmp(msg, msg), 0);
    check("strcmp(ne)", strcmp("a", "b") != 0, 1);

    /* Stateful CLib (malloc/stdio) is the next milestone: the real
     * client's crt0 runs cl_init.s's Initialise (handler registration,
     * slot extenders) before main, and fopen aborts without it.  Pure
     * functions are the proven surface today. */
    OS_Write0(fails ? "clibtest: FAILURES\n" : "clibtest: all ok\n");
    return fails ? 1 : 0;
}
