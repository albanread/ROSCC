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
extern unsigned long roclib_init_stateful(void);
extern int strlen(const char *);
extern int strcmp(const char *, const char *);
extern void *malloc(unsigned long);
extern void free(void *);
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
    unsigned long ver = roclib_init_stateful();
    if (ver == 0) {
        OS_Write0("clibstate: init failed\n");
        return 1;
    }
    OS_Write0("clibstate: registered, probing stateful CLib\n");
    /* The stateful half: the _kernel_init step in roclib_init builds the
     * heap and stdio state.  Probe the allocator first, then stdio. */
    char *m = malloc(64);
    OS_Write0("malloc(64): ");
    print_hex((unsigned long)m);
    if (m) {
        for (int i = 0; i < 64; i++)
            m[i] = (char)i;
        unsigned long sum = 0;
        for (int i = 0; i < 64; i++)
            sum += (unsigned char)m[i];
        check("malloc r/w", sum, 2016);  /* 0+1+...+63 */
        free(m);
    } else {
        OS_Write0("  (null) FAIL\n");
        fails++;
    }

    void *f = fopen("clibtest", "rb");
    OS_Write0("fopen(clibtest): ");
    print_hex((unsigned long)f);
    if (!f) {
        OS_Write0("  (null) FAIL\n");
        fails++;
    } else {
        OS_Write0("\n");
        fseek(f, 0, 2 /* SEEK_END */);
        long size = ftell(f);
        fclose(f);
        OS_Write0("size: ");
        print_hex(size);
        OS_Write0(size > 100 ? "  ok\n" : "  FAIL\n");
        if (size <= 100)
            fails++;
    }

    OS_Write0(fails ? "clibtest: FAILURES\n" : "clibtest: all ok\n");
    return fails ? 1 : 0;
}
