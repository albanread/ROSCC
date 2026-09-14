/* mojopath — the Mojo -> roclib path proven end to end: the Mojo-shaped
 * functions above (compiled from IR, exactly what the compiler emits)
 * call the SharedCLibrary through the veneers, under the library's own
 * _main flow. */

extern void OS_Write0(const char *);
extern void roclib_run(int (*)(int, char **)) __attribute__((noreturn));

/* Mojo-shaped, from mojopath.ll */
extern int mojo_strlen(const char *);
extern int mojo_strcmp(const char *, const char *);
extern void *mojo_malloc(int);
extern const char *mojo_str(void);

static int fails;

static void print_hex(unsigned long v)
{
    char b[11];
    char *p = b + 10;
    *p = 0;
    do { *--p = "0123456789ABCDEF"[v & 15]; v >>= 4; } while (v);
    OS_Write0(p);
}

static void check(const char *what, unsigned long got, unsigned long want)
{
    OS_Write0(what);
    OS_Write0(": got ");
    print_hex(got);
    OS_Write0(" want ");
    print_hex(want);
    OS_Write0(got == want ? "  ok\n" : "  FAIL\n");
    if (got != want) fails++;
}

static int real_main(int argc, char **argv)
{
    const char *s = mojo_str();
    check("mojo strlen", mojo_strlen(s), 23);  /* 0x17 */
    check("mojo strcmp(eq)", mojo_strcmp(s, s), 0);
    char *m = mojo_malloc(64);
    OS_Write0("mojo malloc(64): ");
    print_hex((unsigned long)m);
    OS_Write0(m ? "\n" : "  FAIL(null)\n");
    if (!m) fails++;
    for (int i = 0; i < 64; i++) ((char *)m)[i] = (char)i;
    unsigned long sum = 0;
    for (int i = 0; i < 64; i++) sum += (unsigned char)((char *)m)[i];
    check("mojo heap r/w", sum, 2016);
    OS_Write0(fails ? "mojopath: FAILURES\n" : "mojopath: all ok\n");
    return fails;
}

int main(void) { roclib_run(real_main); }
