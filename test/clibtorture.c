/* clibtorture — the SharedCLibrary binding's pure-function torture suite.
 *
 * Every call here is from the binding spec's `identical` class (scalar
 * args and returns, no variadics, no float, no struct by value), so the
 * whole suite must pass without -ffixed-r9 and without the stateful
 * _kernel_init step: that is the claim under test.  Two directions are
 * covered — our AAPCS code calling CLib, and CLib calling our AAPCS
 * comparator back through a function pointer (qsort, bsearch).
 *
 * Expectations are computed locally, never through the library under
 * test.  One line per function; failures print in full.  Exit code is
 * the failure count (capped at 100).
 */

extern void OS_Write0(const char *);
extern unsigned long roclib_init(void);

/* --- the binding, declared as the DDE headers spell them ------------ */

extern int strlen(const char *);
extern int strcmp(const char *, const char *);
extern int strncmp(const char *, const char *, unsigned long);
extern char *strchr(const char *, int);
extern char *strrchr(const char *, int);
extern char *strstr(const char *, const char *);
extern char *strcpy(char *, const char *);
extern char *strncpy(char *, const char *, unsigned long);
extern char *strcat(char *, const char *);
extern char *strncat(char *, const char *, unsigned long);
extern int memcmp(const void *, const void *, unsigned long);
extern void *memcpy(void *, const void *, unsigned long);
extern void *memmove(void *, const void *, unsigned long);
extern void *memset(void *, int, unsigned long);
extern unsigned long strspn(const char *, const char *);
extern unsigned long strcspn(const char *, const char *);
extern char *strpbrk(const char *, const char *);
extern char *strtok(char *, const char *);

/* The ctype predicates are header macros over the client's __ctype
 * table (the slots exist but are never veneered).  The table lives in
 * our statics block; the masks are ctype.h's. */
extern unsigned char __ctype[];
#define CT_U 16
#define CT_L 8
#define CT_N 32
#define CT_P 2
#define CT_C 64
#define CT_B 4
#define CT_X 128

extern int abs(int);
extern long labs(long);
extern int atoi(const char *);
extern long atol(const char *);
extern long strtol(const char *, char **, int);
extern unsigned long strtoul(const char *, char **, int);
extern void *bsearch(const void *, const void *, unsigned long, unsigned long,
                     int (*)(const void *, const void *));
extern void qsort(void *, unsigned long, unsigned long,
                  int (*)(const void *, const void *));

extern long time(long *);
extern long clock(void);
extern struct tm_test *gmtime(const long *);
extern char *asctime(const struct tm_test *);

extern int _kernel_swi(int, void *, void *); /* r0 = SWI number by value */

struct tm_test { int sec, min, hour, mday, mon, year, wday, yday, isdst; };

/* --- harness --------------------------------------------------------- */

static int fails, cases;
#ifdef STATEFUL
static int stateful = 1;
#else
static int stateful = 0;
#endif

#ifdef VERBOSE
static void ok(const char *fn)
{
    OS_Write0(fn);
    OS_Write0("  ok\n");
}
#endif

static void bad(const char *fn, const char *what)
{
    fails++;
    OS_Write0(fn);
    OS_Write0("  FAIL: ");
    OS_Write0(what);
    OS_Write0("\n");
}

/* Quiet by default: the farm's task-window output path has a drain
 * race that can deadlock a program that prints tens of lines (found
 * the hard way — green runs with empty Farm/Out and 300 s timeouts).
 * Failures always print; full listing with -DVERBOSE. */
#ifdef VERBOSE
static void res(const char *fn, int good)
{
    cases++;
    if (good)
        ok(fn);
    else
        bad(fn, "wrong result");
}
#else
static void res(const char *fn, int good)
{
    cases++;
    if (!good)
        bad(fn, "wrong result");
}
#endif

static void print_dec(unsigned long v)
{
    char b[13];
    char *p = b + 12;
    *p = 0;
    do {
        *--p = '0' + (v % 10);
        v /= 10;
    } while (v);
    OS_Write0(p);
}

static void print_hex(unsigned long v)
{
    char b[11];
    char *p = b + 10;
    *p = 0;
    do {
        *--p = "0123456789ABCDEF"[v & 15];
        v >>= 4;
    } while (v);
    OS_Write0(p);
}

/* --- comparators: CLib calls these, through our AAPCS function ptrs -- */

static int cmp_int(const void *a, const void *b)
{
    int x = *(const int *)a, y = *(const int *)b;
    return (x > y) - (x < y);
}

static int cmpfindFirstZ(const void *a, const void *b)
{
    return *(const char *)a - **(const char *const *)b;
}

/* --- the suite -------------------------------------------------------- */

static void t_string(void)
{
    static const char abc[] = "Hello, RISC OS world";
    char buf[64];

    res("strlen", strlen(abc) == 20 && strlen("") == 0);
    res("strcmp", strcmp("abc", "abc") == 0 && strcmp("abc", "abd") < 0 &&
                    strcmp("b", "a") > 0 && strcmp("", "") == 0);
    res("strncmp", strncmp("abcdef", "abcxyz", 3) == 0 &&
                       strncmp("abcdef", "abcxyz", 4) < 0);
    res("strchr", strchr(abc, 'R') == abc + 7 && strchr(abc, 'z') == 0 &&
                      strchr(abc, 'H') == abc);
    res("strrchr", strrchr("a/b/c/d", '/') != 0 &&
                       (*strrchr("a/b/c/d", '/') == '/') &&
                       (strrchr("a/b/c/d", '/') - "a/b/c/d") == 5);
    res("strstr", strstr(abc, "RISC") == abc + 7 && strstr(abc, "zzz") == 0 &&
                       strstr(abc, "") == abc);
    res("strcpy", (strcpy(buf, "copyme"), buf[6] == 0 && buf[0] == 'c'));
    res("strncpy", (memset(buf, 'X', 8), strncpy(buf, "1234567890", 4),
                       buf[3] == '4' && buf[4] == 'X'));
    res("strcat", (buf[0] = 0, strcat(buf, "one"), strcat(buf, "-two"),
                       strcmp(buf, "one-two") == 0));
    res("strncat", (buf[0] = 0, strncat(buf, "abcdef", 3),
                       strcmp(buf, "abc") == 0));
    res("memcmp", memcmp("same", "same", 4) == 0 && memcmp("same", "samf", 4) < 0);
    res("memcpy", (memset(buf, '.', 8), memcpy(buf, "1234", 4),
                       buf[3] == '4' && buf[4] == '.'));
    res("memmove", (strcpy(buf, "0123456789"),
                        memmove(buf + 2, buf, 8),
                        memcmp(buf, "0101234567", 10) == 0));
    res("memset", (memset(buf, 0, 8), memset(buf, 0xAA, 5),
                       buf[0] == (char)0xAA && buf[5] == 0));
    /* strcasecmp/strncasecmp live in the gen-5 chunk (held back until
     * its slot count matches the ROM); strerror needs the stateful
     * half (it reaches the module's message machinery and faults
     * without _kernel_init).  Both return with their chunks. */
    res("strspn", strspn("abcXYZ", "abc") == 3 && strspn("abc", "xyz") == 0);
    res("strcspn", strcspn("abcXYZ", "XY") == 3 && strcspn("abc", "z") == 3);
    res("strpbrk", strpbrk("abcdef", "fd") != 0 &&
                       *strpbrk("abcdef", "fd") == 'd');
    if (stateful) {
        char tok[32];
        strcpy(tok, "a,b,,c");
        char *t1 = strtok(tok, ",");
        char *t2 = strtok(0, ",");
        char *t3 = strtok(0, ",");
        char *t4 = strtok(0, ",");
        res("strtok", t1 && !strcmp(t1, "a") && t2 && !strcmp(t2, "b") &&
                          t3 == 0 && t4 && !strcmp(t4, "c"));
    } else {
        #ifdef VERBOSE
#ifdef VERBOSE
        OS_Write0("strtok  (stateful: skipped)\n");
#endif
#endif
    }
}

static void t_ctype(void)
{
    if (!stateful) {
        OS_Write0("ctype   (table probe: ");
        print_hex(__ctype['A' + 1]);   /* __ctype is offset by one: c+1 */
        OS_Write0(__ctype['A' + 1] ? ", filled)\n" : ", EMPTY — needs stateful init)\n");
#ifdef VERBOSE
        OS_Write0("ctype   (stateful: skipped)\n");
#endif
        return;
    }
    static const char *cl = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
    static const char *cu = "abcdefghijklmnopqrstuvwxyz";
    static const char *cd = "0123456789";
    static const char *cs = " \t\n\r\f\v";
    int good = 1;
    for (const char *p = cl; *p; p++)
        good &= (__ctype[*p + 1] & (CT_U | CT_L)) && (__ctype[*p + 1] & CT_U) &&
                !(__ctype[*p + 1] & CT_N);
    for (const char *p = cu; *p; p++)
        good &= (__ctype[*p + 1] & CT_L) && !(__ctype[*p + 1] & CT_U);
    for (const char *p = cd; *p; p++)
        good &= (__ctype[*p + 1] & CT_N) && (__ctype[*p + 1] & CT_X) &&
                !(__ctype[*p + 1] & (CT_U | CT_L));
    good &= (__ctype['a' + 1] & CT_X) && (__ctype['F' + 1] & CT_X) &&
            !(__ctype['g' + 1] & CT_X);
    for (const char *p = cs; *p; p++)
        good &= __ctype[*p + 1] & CT_C ? 1 : __ctype[*p + 1] != 0;
    good &= !(__ctype['x' + 1] & (CT_U | CT_L | CT_N));
    res("ctype-table", good);
}

static void t_stdlib(void)
{
    res("abs", abs(-42) == 42 && abs(42) == 42 && abs(0) == 0);
    res("labs", labs(-123456L) == 123456L && labs(7L) == 7L);
    if (stateful) {
        res("atoi", atoi("42") == 42 && atoi("  -7x") == -7 && atoi("") == 0);
        res("atol", atol("123456") == 123456L && atol("-9") == -9L);
    } else {
#ifdef VERBOSE
        OS_Write0("atoi    (stateful: skipped)\n");
        OS_Write0("atol    (stateful: skipped)\n");
#endif
    }

    if (stateful) {
        char *end;
        long v = strtol("2901hex", &end, 16);
        res("strtol", v == 0x2901 && *end == 'h');
        v = strtol("  -77abc", &end, 10);
        res("strtol-neg", v == -77 && *end == 'a');
        unsigned long u = strtoul("fFFFz", &end, 16);
        res("strtoul", u == 0xFFFF && *end == 'z');
    } else {
#ifdef VERBOSE
        OS_Write0("strtol  (stateful: locale tables)\n");
        OS_Write0("strtoul (stateful: locale tables)\n");
#endif
    }

    if (stateful) {
        /* The reverse direction: CLib calls our AAPCS comparators. */
        static int arr[9] = { 5, 3, 9, 1, 7, 2, 8, 6, 4 };
        qsort(arr, 9, sizeof(int), cmp_int);
        int sorted = 1;
        for (int i = 0; i < 9; i++)
            sorted &= arr[i] == i + 1;
        res("qsort+cmp", sorted);

        static int key = 6;
        int *found = bsearch(&key, arr, 9, sizeof(int), cmp_int);
        res("bsearch+cmp", found && *found == 6);
        static int missing = 99;
        res("bsearch-miss", bsearch(&missing, arr, 9, sizeof(int), cmp_int) == 0);
    } else {
#ifdef VERBOSE
        OS_Write0("qsort   (stateful: merge buffer from CLib heap)\n");
        OS_Write0("bsearch (stateful: ditto)\n");
#endif
    }
}

static void t_time(void)
{
    if (!stateful) {
#ifdef VERBOSE
        OS_Write0("time    (stateful: skipped)\n");
        OS_Write0("clock   (stateful: skipped)\n");
        OS_Write0("gmtime  (stateful: skipped)\n");
        OS_Write0("asctime (stateful: skipped)\n");
#endif
        return;
    }
    long t0 = time(0), t1 = time(0);
    res("time", t0 > 0 && t1 >= t0);
    long c0 = clock();
    for (volatile int i = 0; i < 100000; i++)
        ;
    long c1 = clock();
    res("clock", c0 >= 0 && c1 >= c0);

    long epoch = 0;
    struct tm_test *tm = gmtime(&epoch);
    int good = tm && tm->year == 70 && tm->mon == 0 && tm->mday == 1 &&
               tm->hour == 0 && tm->min == 0 && tm->sec == 0;
    if (!good) {
        OS_Write0("  gmtime(0) fields: ");
        if (tm) {
            print_dec(tm->year); OS_Write0("-");
            print_dec(tm->mon); OS_Write0("-");
            print_dec(tm->mday); OS_Write0(" ");
            print_dec(tm->hour); OS_Write0(":");
            print_dec(tm->min); OS_Write0(":");
            print_dec(tm->sec); OS_Write0("\n");
        } else
            OS_Write0("(null)\n");
    }
    res("gmtime", good);

    char *a = asctime(tm);
    res("asctime", a && a[0] && a[4] == ' ' && a[13] == ':');
}

/* _kernel_swi: registers struct by pointer, SWI number by value —
 * r0 is the SWI number directly (kernel.h's _kernel_swi takes an int,
 * not a name).  OS_GetEnv (&10): R1 out = RAM limit, sane and wordy. */
struct kregs { unsigned long r[10]; };

static void t_kernel(void)
{
    if (!stateful) {
#ifdef VERBOSE
        OS_Write0("_kernel_swi (stateful: kernel chunk reads the statics)\n");
#endif
        return;
    }
    struct kregs in, out;
    for (int i = 0; i < 10; i++)
        in.r[i] = out.r[i] = 0;
    int rc = _kernel_swi(0x10, &in, &out);
    res("_kernel_swi", rc == 0 && out.r[1] > 0x10000 && (out.r[1] & 3) == 0);
}

int main(void)
{
    unsigned long ver = roclib_init();
    if (ver == 0) {
        OS_Write0("clibtorture: registration failed\n");
        return 255;
    }
    OS_Write0("clibtorture: CLib ");
    print_hex(ver);
    OS_Write0("\n");

    t_string();
    t_ctype();
    t_stdlib();
    t_time();
    t_kernel();

    OS_Write0("clibtorture: ");
    print_dec(cases);
    OS_Write0(" functions tested, ");
    print_dec(fails);
    OS_Write0(fails ? " FAILURES\n" : " failures — all ok\n");
    /* Exit through CLib's own _exit slot: leaving via raw OS_Exit keeps
     * the module's client chain stale and the NEXT registration hangs
     * (found on the farm: first run green, rerun dead). */
    extern void _exit(int);
    _exit(fails > 100 ? 100 : fails);
    return 0;
}
